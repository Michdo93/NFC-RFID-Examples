#!/usr/bin/env python3
"""
04_controller.py – Haupt-Controller: Karten → openHAB
======================================================
Das Herzstück des Systems. Erkennt Karten, misst Haltezeit
und führt die konfigurierten Aktionen aus.

Features:
  ✓ Toggle (Switch, Dimmer ON/OFF)
  ✓ Dimmen in Schritten (Dauerhalten = kontinuierlich)
  ✓ Farbe setzen
  ✓ Farb-Zyklus (kurz = nächste, lang = endlos)
  ✓ Szenen aktivieren
  ✓ State-Save / State-Restore
  ✓ Rollladen steuern
  ✓ Player steuern (PLAY, PAUSE, NEXT, ...)

Timing:
  < SHORT_PRESS_MIN → ignoriert
  0.3 – 4s → Kurz-Aktion (toggle, farbe setzen, szene, restore)
  > 4s → Lang-Aktion läuft solange Karte liegt (dimmen, farb-zyklus)

Verwendung:
  python3 04_controller.py
  python3 04_controller.py --verbose     → Debug-Ausgabe
  python3 04_controller.py --no-write    → Nur lesen, nichts an openHAB senden
"""

import time
import json
import argparse
import logging
import threading
from pathlib import Path
from typing import Optional

from acr122u import ACR122U
from openhab_api import OpenHABAPI
from config import (
    OPENHAB_URL, OPENHAB_TOKEN, OPENHAB_USERNAME, OPENHAB_PASSWORD,
    ALL_MANAGED_ITEMS, STATE_SAVE_ITEMS,
    SWITCH_ITEMS, DIMMER_ITEMS, COLOR_ITEMS,
    ROLLERSHUTTER_ITEMS, PLAYER_ITEMS,
    CARD_ACTIONS, COLORS, COLOR_CYCLE_ORDER, SCENES,
    DIM_STEP, DIM_INTERVAL, DIM_MIN, DIM_MAX,
    LONG_PRESS_THRESHOLD, SHORT_PRESS_MIN,
    STATE_SAVE_WINDOW, COLOR_CYCLE_INTERVAL,
    STATE_FILE,
)

# ──────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

DRY_RUN = False   # Wenn True: keine openHAB-Befehle senden


def log_action(label: str, action: str, detail: str = ""):
    """Formatierte Ausgabe einer Aktion."""
    ts = time.strftime("%H:%M:%S")
    detail_str = f" → {detail}" if detail else ""
    print(f"[{ts}] 🃏 {label:<30} {action}{detail_str}")


# ──────────────────────────────────────────────────────────────────────
# State-Persistenz
# ──────────────────────────────────────────────────────────────────────

class StatePersistence:
    """Verwaltet gespeicherte Zustände (JSON-Datei)."""

    def __init__(self, filepath: str):
        self.path = Path(filepath)
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning(f"State-Datei nicht lesbar: {e}")
                self._data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"State-Datei nicht schreibbar: {e}")

    def save_slot(self, slot_name: str, snapshot: dict):
        """Speichert einen Snapshot unter einem Slot-Namen."""
        self._data[slot_name] = {
            "timestamp": time.time(),
            "snapshot": snapshot,
        }
        self._save()
        logger.info(f"State gespeichert: {slot_name} ({len(snapshot)} Items)")

    def load_slot(self, slot_name: str) -> Optional[dict]:
        """Lädt einen Snapshot aus einem Slot."""
        if slot_name not in self._data:
            return None
        return self._data[slot_name].get("snapshot")

    def list_slots(self) -> list:
        """Gibt alle gespeicherten Slot-Namen zurück."""
        return list(self._data.keys())


# ──────────────────────────────────────────────────────────────────────
# Aktions-Handler
# ──────────────────────────────────────────────────────────────────────

class ActionHandler:
    """
    Führt Aktionen basierend auf dem Aktions-String aus.
    Unterscheidet zwischen kurzen und langen Haltezeiten.
    """

    def __init__(self, api: OpenHABAPI, state_db: StatePersistence):
        self.api = api
        self.state_db = state_db
        self._stop_long_press = threading.Event()
        self._long_press_thread: Optional[threading.Thread] = None
        self._state_save_mode = False
        self._state_save_deadline: float = 0.0
        self._color_cycle_state: dict = {}  # item → aktueller index

    def handle_card_placed(self, uid: str, card_info: dict):
        """Wird aufgerufen wenn eine Karte aufgelegt wird."""
        action_str = card_info.get("action", "")
        label = card_info.get("label", uid)

        # State-Save Modus: Diese Karte soll den Zustand speichern
        if self._state_save_mode and time.time() < self._state_save_deadline:
            self._do_state_save_to_card(uid, label)
            self._state_save_mode = False
            return

        # Normale Aktion starten (kurz-Aktion sofort, lang-Aktion vorbereiten)
        self._start_action(uid, label, action_str)

    def handle_card_removed(self, uid: str, card_info: dict, hold_time: float):
        """Wird aufgerufen wenn eine Karte entfernt wird."""
        # Lang-Aktion stoppen
        self._stop_long_press.set()
        if self._long_press_thread and self._long_press_thread.is_alive():
            self._long_press_thread.join(timeout=1.0)

        action_str = card_info.get("action", "")
        label = card_info.get("label", uid)

        logger.debug(f"Karte entfernt: {label} (gehalten: {hold_time:.1f}s)")

        # Kurz-Aktion wenn Haltezeit im Kurz-Bereich
        if SHORT_PRESS_MIN <= hold_time < LONG_PRESS_THRESHOLD:
            self._execute_short_action(uid, label, action_str)

    def _start_action(self, uid: str, label: str, action_str: str):
        """Startet die Aktion (lang-Aktionen starten sofort im Hintergrund)."""
        parts = action_str.split(":")

        action_type = parts[0] if parts else ""

        # Lang-Aktionen starten sofort
        if action_type in ("dim_up", "dim_down", "color_cycle"):
            self._stop_long_press.clear()
            self._long_press_thread = threading.Thread(
                target=self._long_press_loop,
                args=(uid, label, action_str),
                daemon=True
            )
            self._long_press_thread.start()

    def _execute_short_action(self, uid: str, label: str, action_str: str):
        """Führt eine Kurz-Aktion aus (nach Karte-Entfernen)."""
        parts = action_str.split(":")
        action_type = parts[0] if parts else ""

        if action_type == "toggle":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = ALL_MANAGED_ITEMS.get(item)
            if oh_item:
                log_action(label, "TOGGLE", oh_item)
                if not DRY_RUN:
                    self.api.toggle_switch(oh_item)

        elif action_type == "dim_up":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = DIMMER_ITEMS.get(item)
            if oh_item:
                log_action(label, f"DIM +{DIM_STEP}%", oh_item)
                if not DRY_RUN:
                    self.api.dim_step(oh_item, DIM_STEP)

        elif action_type == "dim_down":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = DIMMER_ITEMS.get(item)
            if oh_item:
                log_action(label, f"DIM -{DIM_STEP}%", oh_item)
                if not DRY_RUN:
                    self.api.dim_step(oh_item, -DIM_STEP)

        elif action_type == "color":
            item = parts[1] if len(parts) > 1 else ""
            color = parts[2] if len(parts) > 2 else ""
            oh_item = COLOR_ITEMS.get(item)
            if oh_item and color in COLORS:
                log_action(label, f"FARBE {color}", oh_item)
                if not DRY_RUN:
                    self.api.set_color_by_name(oh_item, color, COLORS)

        elif action_type == "color_cycle":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = COLOR_ITEMS.get(item)
            if oh_item:
                # Nächste Farbe im Zyklus
                idx = self._color_cycle_state.get(item, 0)
                next_color = COLOR_CYCLE_ORDER[idx % len(COLOR_CYCLE_ORDER)]
                log_action(label, f"FARB-ZYKLUS → {next_color}", oh_item)
                if not DRY_RUN:
                    self.api.set_color_by_name(oh_item, next_color, COLORS)
                self._color_cycle_state[item] = idx + 1

        elif action_type == "scene":
            scene_name = parts[1] if len(parts) > 1 else ""
            if scene_name in SCENES:
                log_action(label, f"SZENE: {scene_name}")
                if not DRY_RUN:
                    sent = self.api.activate_scene(
                        scene_name, SCENES, ALL_MANAGED_ITEMS
                    )
                    print(f"  → {sent} Items gesetzt")
            else:
                print(f"  ✗ Szene '{scene_name}' nicht gefunden")

        elif action_type == "state_save":
            # State-Save Modus aktivieren
            self._state_save_mode = True
            self._state_save_deadline = time.time() + STATE_SAVE_WINDOW
            log_action(label, "STATE-SAVE MODUS", f"{STATE_SAVE_WINDOW:.0f}s Fenster")
            print(f"  → Lege jetzt die Ziel-Karte auf (innerhalb {STATE_SAVE_WINDOW:.0f}s)")

        elif action_type == "state_restore":
            slot = parts[1] if len(parts) > 1 else ""
            self._do_state_restore(label, slot)

        elif action_type == "game_toggle":
            log_action(label, "SPIEL TOGGLE")
            if not DRY_RUN:
                # Triggert das Spiel-Item in openHAB
                from config import GAME_WIN_ITEM
                self.api.toggle_switch(GAME_WIN_ITEM)

        elif action_type == "rollershutter_toggle":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = ROLLERSHUTTER_ITEMS.get(item)
            if oh_item:
                log_action(label, "ROLLO TOGGLE", oh_item)
                if not DRY_RUN:
                    self.api.rollershutter_toggle(oh_item)

        elif action_type == "rollershutter_up":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = ROLLERSHUTTER_ITEMS.get(item)
            if oh_item:
                log_action(label, "ROLLO UP", oh_item)
                if not DRY_RUN:
                    self.api.send_command(oh_item, "UP")

        elif action_type == "rollershutter_down":
            item = parts[1] if len(parts) > 1 else ""
            oh_item = ROLLERSHUTTER_ITEMS.get(item)
            if oh_item:
                log_action(label, "ROLLO DOWN", oh_item)
                if not DRY_RUN:
                    self.api.send_command(oh_item, "DOWN")

        elif action_type == "player":
            item = parts[1] if len(parts) > 1 else ""
            cmd = parts[2].upper() if len(parts) > 2 else "PLAY"
            oh_item = PLAYER_ITEMS.get(item)
            if oh_item:
                if cmd == "PLAY":
                    log_action(label, "PLAYER PLAY/PAUSE", oh_item)
                    if not DRY_RUN:
                        self.api.player_toggle_play(oh_item)
                else:
                    log_action(label, f"PLAYER {cmd}", oh_item)
                    if not DRY_RUN:
                        self.api.player_command(oh_item, cmd)

        else:
            print(f"  ⚠ Unbekannte Aktion: {action_str}")

    def _long_press_loop(self, uid: str, label: str, action_str: str):
        """
        Lang-Press Loop (läuft im Hintergrund solange Karte liegt).
        Für dim_up, dim_down, color_cycle.
        """
        # Warte bis Long-Press-Threshold erreicht ist
        start = time.time()
        while not self._stop_long_press.wait(timeout=0.05):
            if time.time() - start >= LONG_PRESS_THRESHOLD:
                break

        if self._stop_long_press.is_set():
            return  # Karte schon entfernt (Kurz-Press)

        parts = action_str.split(":")
        action_type = parts[0]

        log_action(label, f"LANG-PRESS START: {action_str}")

        # Wiederhole Aktion bis Karte entfernt
        while not self._stop_long_press.is_set():
            if action_type == "dim_up":
                item = parts[1] if len(parts) > 1 else ""
                oh_item = DIMMER_ITEMS.get(item)
                if oh_item and not DRY_RUN:
                    new_val = self.api.dim_step(oh_item, DIM_STEP)
                    if new_val is not None:
                        print(f"  ↑ {oh_item}: {new_val}%")
                    if new_val is not None and new_val >= DIM_MAX:
                        break  # Maximum erreicht

            elif action_type == "dim_down":
                item = parts[1] if len(parts) > 1 else ""
                oh_item = DIMMER_ITEMS.get(item)
                if oh_item and not DRY_RUN:
                    new_val = self.api.dim_step(oh_item, -DIM_STEP)
                    if new_val is not None:
                        print(f"  ↓ {oh_item}: {new_val}%")
                    if new_val is not None and new_val <= DIM_MIN:
                        break  # Minimum erreicht

            elif action_type == "color_cycle":
                item = parts[1] if len(parts) > 1 else ""
                oh_item = COLOR_ITEMS.get(item)
                if oh_item:
                    idx = self._color_cycle_state.get(item, 0)
                    if not DRY_RUN:
                        next_color, next_idx = self.api.cycle_color(
                            oh_item, COLOR_CYCLE_ORDER, COLORS, idx
                        )
                    else:
                        next_idx = (idx + 1) % len(COLOR_CYCLE_ORDER)
                        next_color = COLOR_CYCLE_ORDER[next_idx]
                    print(f"  🎨 {oh_item}: {next_color}")
                    self._color_cycle_state[item] = next_idx
                    self._stop_long_press.wait(timeout=COLOR_CYCLE_INTERVAL)
                    continue

            self._stop_long_press.wait(timeout=DIM_INTERVAL)

        logger.debug(f"Lang-Press Ende: {label}")

    def _do_state_save_to_card(self, uid: str, label: str):
        """Speichert aktuellen Zustand aller STATE_SAVE_ITEMS."""
        log_action(label, "STATE SPEICHERN")
        if not DRY_RUN:
            snapshot = self.api.get_states_snapshot(STATE_SAVE_ITEMS)
            slot_name = f"card_{uid.replace(':', '_')}"
            self.state_db.save_slot(slot_name, snapshot)
            print(f"  ✓ {len(snapshot)} Items gespeichert → Slot: {slot_name}")
            for k, v in snapshot.items():
                print(f"    {k}: {v}")
        else:
            print("  [DRY-RUN] State würde gespeichert werden")

    def _do_state_restore(self, label: str, slot: str):
        """Stellt einen gespeicherten Zustand wieder her."""
        log_action(label, "STATE WIEDERHERSTELLEN", slot)
        if DRY_RUN:
            print("  [DRY-RUN] State würde wiederhergestellt werden")
            return

        snapshot = self.state_db.load_slot(slot)
        if snapshot is None:
            print(f"  ✗ Kein gespeicherter Zustand für Slot: {slot}")
            print(f"  Verfügbare Slots: {self.state_db.list_slots()}")
            return

        restored = self.api.restore_states_snapshot(
            snapshot, STATE_SAVE_ITEMS, use_post_update=True
        )
        print(f"  ✓ {restored}/{len(snapshot)} Items wiederhergestellt")
        for k, v in snapshot.items():
            print(f"    {k}: {v}")


# ──────────────────────────────────────────────────────────────────────
# Haupt-Schleife
# ──────────────────────────────────────────────────────────────────────

def main_loop(api: OpenHABAPI, state_db: StatePersistence):
    """Haupt-Polling-Schleife: erkennt Karten und dispatcht Aktionen."""

    handler = ActionHandler(api, state_db)
    reader = ACR122U()
    current_uid: Optional[str] = None
    card_placed_time: float = 0.0

    print("\n" + "═"*55)
    print("   RFID/NFC → openHAB Controller")
    print("   (Ctrl+C zum Beenden)")
    if DRY_RUN:
        print("   ⚠ DRY-RUN: Keine Befehle werden gesendet")
    print("═"*55)

    # openHAB-Verbindung testen
    if not DRY_RUN:
        if api.test_connection():
            print(f"  ✓ openHAB erreichbar: {OPENHAB_URL}")
        else:
            print(f"  ✗ openHAB NICHT erreichbar: {OPENHAB_URL}")
            print("    Überprüfe URL und Netzwerk in config.py")

    print(f"\n  {len(CARD_ACTIONS)} Karte(n) konfiguriert")
    print("\n>>> Lege eine Karte auf...\n")

    try:
        while True:
            # Karte erkennen
            if reader.connect():
                uid = reader.get_uid()

                if uid and uid != current_uid:
                    # Neue Karte erkannt
                    if uid in CARD_ACTIONS:
                        card_info = CARD_ACTIONS[uid]
                        current_uid = uid
                        card_placed_time = time.time()
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] 📇 Karte: {card_info['label']} ({uid})")
                        handler.handle_card_placed(uid, card_info)
                    else:
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] ❓ Unbekannte Karte: {uid}")
                        print(f"       → In config.py eintragen oder mit 01_write_cards.py beschreiben")
                        current_uid = uid  # Trotzdem merken (verhindert Spam)
                        card_placed_time = time.time()

                elif not uid and current_uid:
                    # Karte entfernt
                    hold_time = time.time() - card_placed_time
                    if current_uid in CARD_ACTIONS:
                        card_info = CARD_ACTIONS[current_uid]
                        handler.handle_card_removed(current_uid, card_info, hold_time)
                    else:
                        # Unbekannte Karte entfernt → nichts tun
                        pass
                    current_uid = None
                    reader.disconnect()

                time.sleep(0.1)
            else:
                # Kein Reader oder keine Karte
                if current_uid:
                    # Karte verschwunden
                    hold_time = time.time() - card_placed_time
                    if current_uid in CARD_ACTIONS:
                        card_info = CARD_ACTIONS[current_uid]
                        handler.handle_card_removed(current_uid, card_info, hold_time)
                    current_uid = None
                time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n\n  Controller beendet.")


# ──────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID/NFC → openHAB Controller")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug-Ausgabe aktivieren")
    parser.add_argument("--no-write", "--dry-run", action="store_true",
                        help="Nur lesen, nichts an openHAB senden")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s [%(name)s]: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if args.no_write:
        DRY_RUN = True

    # Services initialisieren
    api = OpenHABAPI(
        base_url=OPENHAB_URL,
        token=OPENHAB_TOKEN,
        username=OPENHAB_USERNAME,
        password=OPENHAB_PASSWORD,
    )
    state_db = StatePersistence(STATE_FILE)

    main_loop(api, state_db)
