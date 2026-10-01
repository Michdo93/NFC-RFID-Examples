#!/usr/bin/env python3
"""
05_game.py – Lampen-Zuordnungsspiel
=====================================
Spielprinzip:
  - 28 Lampen, 28 Karten, jede Karte gehört fest zu einer Lampe
  - Eine Lampe blinkt als "Aufgabe"
  - Spieler legt Karten auf, bis die richtige gefunden ist
  - Richtige Karte → Lampe bleibt AN, nächste Lampe blinkt
  - Falsche Karte → Lampe leuchtet kurz (2s) → geht wieder aus
  - Bereits richtig erkannte Lampen gehen NIE wieder aus (solange Spiel läuft)
  - Gewonnen wenn alle Lampen AN

openHAB Items:
  Switch_GameRunning  → ON während Spiel läuft
  Number_GameLevel    → Anzahl bereits gelöster Lampen (0–28)
  String_GameState    → "Idle" / "Running" / "Gewonnen"
  Switch_GameBlink    → kurz ON setzen triggert openHAB-Blink-Regel

Verwendung:
  python3 05_game.py
  python3 05_game.py --dry-run   → keine openHAB-Befehle
  python3 05_game.py --verbose   → Debug-Ausgabe
"""

import time
import argparse
import logging
from typing import Optional

from acr122u import ACR122U
from openhab_api import OpenHABAPI
from config import (
    OPENHAB_URL, OPENHAB_TOKEN, OPENHAB_USERNAME, OPENHAB_PASSWORD,
    GAME_LAMP_SEQUENCE,
    GAME_RUNNING_ITEM,
    GAME_LEVEL_ITEM,
    GAME_STATE_ITEM,
    GAME_BLINK_ITEM,
    CARD_ACTIONS,
)

logger = logging.getLogger(__name__)
DRY_RUN = False

# ──────────────────────────────────────────────────────────────────────
# Timing-Konstanten (hier anpassen)
# ──────────────────────────────────────────────────────────────────────
WRONG_LAMP_ON_DURATION  = 2.0   # Sekunden: falsche Karte → Lampe kurz an
BLINK_ON_TIME           = 0.4   # Sekunden: Blink-Phase AN
BLINK_OFF_TIME          = 0.3   # Sekunden: Blink-Phase AUS
BLINK_COUNT             = 3     # Wie oft blinkt die gesuchte Lampe zur Ankündigung
NEXT_LAMP_PAUSE         = 1.0   # Pause zwischen "Richtig!" und nächster Lampe blinkt
CARD_POLL_INTERVAL      = 0.15  # Sekunden: Reader-Polling-Intervall


# ──────────────────────────────────────────────────────────────────────
# Lampen-Steuerung
# ──────────────────────────────────────────────────────────────────────

def lamp_on(api: OpenHABAPI, item_name: str):
    """Schaltet eine Lampe ein."""
    if not DRY_RUN:
        api.send_command(item_name, "ON")

def lamp_off(api: OpenHABAPI, item_name: str):
    """Schaltet eine Lampe aus."""
    if not DRY_RUN:
        api.send_command(item_name, "OFF")

def lamp_blink(api: OpenHABAPI, item_name: str,
               count: int = BLINK_COUNT,
               on_time: float = BLINK_ON_TIME,
               off_time: float = BLINK_OFF_TIME):
    """
    Lässt eine Lampe `count` mal blinken.
    Danach ist die Lampe AUS.
    Signalisiert: "Such meine Karte!"
    """
    if DRY_RUN:
        print(f"  [DRY] {item_name} blinkt {count}x")
        return
    for _ in range(count):
        api.send_command(item_name, "ON")
        time.sleep(on_time)
        api.send_command(item_name, "OFF")
        time.sleep(off_time)

def all_lamps_off(api: OpenHABAPI, lamp_sequence: list):
    """Schaltet alle Spiellampen aus."""
    if DRY_RUN:
        print("  [DRY] Alle Lampen aus")
        return
    for _uid, item_name, _label in lamp_sequence:
        api.send_command(item_name, "OFF")
        time.sleep(0.05)   # kurze Pause damit openHAB nicht überflutet wird

def all_lamps_on(api: OpenHABAPI, lamp_sequence: list):
    """Schaltet alle Spiellampen ein (Sieg-Vorbereitung)."""
    if DRY_RUN:
        print("  [DRY] Alle Lampen ein")
        return
    for _uid, item_name, _label in lamp_sequence:
        api.send_command(item_name, "ON")
        time.sleep(0.05)


# ──────────────────────────────────────────────────────────────────────
# openHAB Spiel-State
# ──────────────────────────────────────────────────────────────────────

def set_game_running(api: OpenHABAPI, running: bool):
    """Setzt Switch_GameRunning."""
    if not DRY_RUN:
        api.send_command(GAME_RUNNING_ITEM, "ON" if running else "OFF")

def set_game_level(api: OpenHABAPI, level: int):
    """Setzt Number_GameLevel."""
    if not DRY_RUN:
        api.post_update(GAME_LEVEL_ITEM, str(level))

def set_game_state(api: OpenHABAPI, state: str):
    """Setzt String_GameState: 'Idle', 'Running', 'Gewonnen'."""
    if not DRY_RUN:
        api.post_update(GAME_STATE_ITEM, state)

def is_game_running(api: OpenHABAPI) -> bool:
    """Liest Switch_GameRunning aus openHAB."""
    if DRY_RUN:
        return True
    state = api.get_state(GAME_RUNNING_ITEM)
    return state is not None and state.upper() == "ON"

def trigger_blink(api: OpenHABAPI):
    """
    Triggert die openHAB-Blink-Regel (alle Lampen blinken grün bei Sieg).
    Setzt Switch_GameBlink kurz auf ON.
    """
    if DRY_RUN:
        print("  [DRY] Sieg-Blink getriggert")
        return
    api.send_command(GAME_BLINK_ITEM, "ON")
    time.sleep(0.5)
    api.send_command(GAME_BLINK_ITEM, "OFF")


# ──────────────────────────────────────────────────────────────────────
# Karten-Lesen
# ──────────────────────────────────────────────────────────────────────

def wait_for_card_placed(reader: ACR122U, timeout: float = 300.0) -> Optional[str]:
    """
    Wartet bis eine Karte aufgelegt wird.
    Gibt UID zurück oder None bei Timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if reader.connect():
            uid = reader.get_uid()
            if uid:
                return uid
            reader.disconnect()
        time.sleep(CARD_POLL_INTERVAL)
    return None

def wait_for_card_removed(reader: ACR122U, timeout: float = 30.0):
    """Wartet bis die Karte entfernt wird."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            uid = reader.get_uid()
            if uid is None:
                reader.disconnect()
                return
        except Exception:
            reader.disconnect()
            return
        time.sleep(0.1)
    reader.disconnect()


# ──────────────────────────────────────────────────────────────────────
# Haupt-Spiellogik
# ──────────────────────────────────────────────────────────────────────

def run_game(api: OpenHABAPI):
    """
    Haupt-Spielschleife.
    Geht GAME_LAMP_SEQUENCE nacheinander durch.
    Bei jeder Lampe: warte auf die richtige Karte.
    """
    reader = ACR122U()
    total_lamps = len(GAME_LAMP_SEQUENCE)

    print(f"\n{'═'*55}")
    print(f"   🎮 LAMPEN-ZUORDNUNGSSPIEL")
    print(f"   {total_lamps} Lampen · {total_lamps} Karten")
    print(f"{'═'*55}")

    # ── Spiel initialisieren ─────────────────────────────────────────
    print("\n  Initialisiere...")
    all_lamps_off(api, GAME_LAMP_SEQUENCE)
    set_game_running(api, True)
    set_game_level(api, 0)
    set_game_state(api, "Running")
    time.sleep(0.5)

    solved_count = 0          # Anzahl gelöster Lampen
    # Menge der bereits verbrauchten (richtigen) Karten-UIDs
    used_uids: set = set()

    # ── Lampen nacheinander als Aufgabe präsentieren ─────────────────
    for lamp_index, (correct_uid, lamp_item, lamp_label) in enumerate(GAME_LAMP_SEQUENCE):

        print(f"\n{'─'*55}")
        print(f"  Lampe {lamp_index + 1}/{total_lamps}: {lamp_label}")
        print(f"  Gesuchte Karte: ???  (du weißt es noch nicht 😄)")
        print(f"  Bereits gelöst: {solved_count}")

        # Ankündigung: gesuchte Lampe blinkt
        print(f"\n  💡 {lamp_label} blinkt – leg die passende Karte auf!")
        lamp_blink(api, lamp_item, count=BLINK_COUNT)

        # ── Rateloop: Karten auflegen bis richtige gefunden ──────────
        attempts = 0
        while True:

            # Prüfen ob Spiel von außen abgebrochen wurde
            if not is_game_running(api):
                print("\n  🛑 Spiel wurde von außen beendet (Switch_GameRunning = OFF)")
                _cleanup(api, reader, GAME_LAMP_SEQUENCE, solved_count)
                return

            # Auf Karte warten
            uid = wait_for_card_placed(reader, timeout=300.0)

            if uid is None:
                print("  ⏰ Timeout – Spiel wird beendet")
                _cleanup(api, reader, GAME_LAMP_SEQUENCE, solved_count)
                return

            # Start/Stop-Karte → Spiel abbrechen
            if uid in CARD_ACTIONS and CARD_ACTIONS[uid].get("action") == "game_toggle":
                print("\n  🛑 Spiel manuell abgebrochen")
                wait_for_card_removed(reader)
                _cleanup(api, reader, GAME_LAMP_SEQUENCE, solved_count)
                return

            # Bereits genutzte (gelöste) Karte nochmal aufgelegt → ignorieren
            if uid in used_uids:
                label = _uid_to_label(uid)
                print(f"  ↩ {label} – diese Karte ist schon einer Lampe zugeordnet")
                wait_for_card_removed(reader)
                continue

            attempts += 1
            card_label = _uid_to_label(uid)
            print(f"  🃏 Versuch {attempts}: {card_label} ({uid})")

            # ── Richtige Karte? ───────────────────────────────────────
            if uid == correct_uid:
                print(f"  ✅ RICHTIG! {lamp_label} gehört zu: {card_label}")
                lamp_on(api, lamp_item)   # Lampe bleibt AN
                wait_for_card_removed(reader)

                solved_count += 1
                used_uids.add(uid)
                set_game_level(api, solved_count)

                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}] Gelöst: {solved_count}/{total_lamps}")

                # Kurze Pause vor nächster Lampe
                time.sleep(NEXT_LAMP_PAUSE)
                break   # → nächste Lampe in der äußeren Schleife

            # ── Falsche Karte ────────────────────────────────────────
            else:
                # Herausfinden welche Lampe diese Karte eigentlich steuert
                wrong_lamp_item, wrong_lamp_label = _uid_to_lamp(uid)

                if wrong_lamp_item:
                    print(f"  ❌ Falsch! Diese Karte gehört zu: {wrong_lamp_label}")
                    # Diese (falsche) Lampe kurz aufleuchten lassen
                    lamp_on(api, wrong_lamp_item)
                    wait_for_card_removed(reader)
                    time.sleep(WRONG_LAMP_ON_DURATION)
                    # Nur ausschalten wenn diese Lampe nicht bereits gelöst war
                    if uid not in used_uids:
                        lamp_off(api, wrong_lamp_item)
                else:
                    # Karte ist gar nicht im Spiel registriert
                    print(f"  ❓ Unbekannte Karte – nicht im Spiel registriert")
                    wait_for_card_removed(reader)

    # ── Alle Lampen gelöst: GEWONNEN ────────────────────────────────
    _victory(api, reader, total_lamps)


def _uid_to_label(uid: str) -> str:
    """Gibt das Label einer UID zurück (aus CARD_ACTIONS oder GAME_LAMP_SEQUENCE)."""
    # Erst in Spielkarten suchen
    for card_uid, _item, label in GAME_LAMP_SEQUENCE:
        if card_uid == uid:
            return label
    # Dann in CARD_ACTIONS
    if uid in CARD_ACTIONS:
        return CARD_ACTIONS[uid].get("label", uid)
    return uid


def _uid_to_lamp(uid: str) -> tuple:
    """
    Sucht die Lampe zu einer UID in GAME_LAMP_SEQUENCE.
    Gibt (item_name, label) zurück oder (None, None) wenn nicht gefunden.
    """
    for card_uid, item_name, label in GAME_LAMP_SEQUENCE:
        if card_uid == uid:
            return item_name, label
    return None, None


def _cleanup(api: OpenHABAPI, reader: ACR122U,
             lamp_sequence: list, solved_count: int):
    """Aufräumen nach Spielabbruch."""
    print(f"\n  Spiel beendet. Gelöste Lampen: {solved_count}/{len(lamp_sequence)}")
    set_game_running(api, False)
    set_game_state(api, "Idle")

    # Alle Lampen ausschalten (auch die bereits gelösten)
    print("  Lampen werden ausgeschaltet...")
    time.sleep(1.5)
    all_lamps_off(api, lamp_sequence)
    reader.disconnect()


def _victory(api: OpenHABAPI, reader: ACR122U, total_lamps: int):
    """Sieg-Sequenz."""
    print(f"\n{'═'*55}")
    print(f"   🏆 GEWONNEN! Alle {total_lamps} Lampen gefunden!")
    print(f"{'═'*55}")

    # openHAB Items setzen
    set_game_level(api, total_lamps)
    set_game_state(api, "Gewonnen")

    # openHAB-Blink-Regel triggern (alle Lampen blinken grün)
    print("  ✨ Sieg-Blink wird getriggert...")
    trigger_blink(api)

    # openHAB-Regel braucht etwas Zeit zum Blinken
    time.sleep(5.0)

    # Spiel beenden
    set_game_running(api, False)

    # Lampen ausschalten
    print("  Lampen werden ausgeschaltet...")
    all_lamps_off(api, GAME_LAMP_SEQUENCE)

    reader.disconnect()
    print("  Spiel zurückgesetzt. Bereit für neue Runde.")


# ──────────────────────────────────────────────────────────────────────
# Warte auf Spielstart (Haupt-Wartemodus)
# ──────────────────────────────────────────────────────────────────────

def wait_for_game_start(api: OpenHABAPI):
    """
    Wartet im Idle-Modus auf die Start/Stop-Karte oder
    darauf, dass Switch_GameRunning von openHAB auf ON gesetzt wird.
    """
    reader = ACR122U()

    print(f"\n{'═'*55}")
    print(f"   🎮 LAMPEN-ZUORDNUNGSSPIEL – Wartemodus")
    print(f"   Lege die Start-Karte auf ODER setze")
    print(f"   {GAME_RUNNING_ITEM} in openHAB auf ON")
    print(f"   (Ctrl+C zum Beenden)")
    print(f"{'═'*55}\n")

    set_game_state(api, "Idle")
    set_game_level(api, 0)

    try:
        while True:
            # Karte aufgelegt?
            if reader.connect():
                uid = reader.get_uid()
                if uid:
                    action = CARD_ACTIONS.get(uid, {}).get("action", "")
                    if action == "game_toggle":
                        print(f"  ▶ Start-Karte erkannt – Spiel startet!")
                        wait_for_card_removed(reader)
                        run_game(api)
                        # Nach Spiel: zurück in Wartemodus
                        print(f"\n  Zurück im Wartemodus...")
                    else:
                        # Andere Karte im Wartemodus → ignorieren
                        label = CARD_ACTIONS.get(uid, {}).get("label", uid)
                        print(f"  (Karte im Wartemodus ignoriert: {label})")
                        wait_for_card_removed(reader)
                reader.disconnect()

            # Switch_GameRunning von openHAB auf ON gesetzt?
            elif is_game_running(api):
                print(f"  ▶ {GAME_RUNNING_ITEM} = ON erkannt – Spiel startet!")
                run_game(api)
                print(f"\n  Zurück im Wartemodus...")

            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n\n  Beendet.")


# ──────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Lampen-Zuordnungsspiel mit RFID-Karten"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Keine openHAB-Befehle senden")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug-Ausgabe")
    parser.add_argument("--start", action="store_true",
                        help="Spiel sofort starten ohne auf Start-Karte zu warten")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s [%(name)s]: %(message)s"
        )
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if args.dry_run:
        DRY_RUN = True
        print("  ⚠ DRY-RUN: Keine openHAB-Befehle werden gesendet")

    api = OpenHABAPI(
        base_url=OPENHAB_URL,
        token=OPENHAB_TOKEN,
        username=OPENHAB_USERNAME,
        password=OPENHAB_PASSWORD,
    )

    if not DRY_RUN and not api.test_connection():
        print(f"  ⚠ openHAB nicht erreichbar: {OPENHAB_URL}")
        print("  Starte im DRY-RUN Modus...")
        DRY_RUN = True

    if args.start:
        run_game(api)
    else:
        wait_for_game_start(api)