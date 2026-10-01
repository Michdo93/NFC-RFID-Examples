"""
openhab_api.py – openHAB Wrapper auf Basis von python-openhab-rest-client
==========================================================================
Kapselt alle openHAB-Operationen für das RFID/NFC-Steuerprojekt.

Installieren:
    pip install python-openhab-rest-client

Library-Importe:
    from openhab import OpenHABClient, Items

Alle openHAB-Zugriffe laufen über items.sendCommand() und items.getItemState().
Für State-Restore wird items.postUpdate() genutzt (setzt State direkt).
"""

import logging
from typing import Optional

try:
    from openhab import OpenHABClient, Items
except ImportError:
    raise ImportError(
        "python-openhab-rest-client nicht installiert!\n"
        "  pip install python-openhab-rest-client"
    )

logger = logging.getLogger(__name__)


class OpenHABAPI:
    """
    Wrapper um python-openhab-rest-client für RFID/NFC-Steuerung.

    Verwendung:
        api = OpenHABAPI("http://192.168.1.100:8080", token="oh.xxx")
        api.send_command("Light_Wohnzimmer", "ON")
        state = api.get_state("Dimmer_Wohnzimmer")
    """

    def __init__(self, base_url: str, token: str = "",
                 username: str = "", password: str = ""):
        """
        Verbindung herstellen.
        Bevorzugt Token-Auth (Standard in openHAB 3+).
        Fallback auf Basic-Auth wenn username+password angegeben.
        """
        if token:
            self._client = OpenHABClient(url=base_url, token=token)
        elif username and password:
            self._client = OpenHABClient(url=base_url,
                                         username=username,
                                         password=password)
        else:
            # Ohne Auth – funktioniert bei lokalen openHAB-Instanzen
            # ohne aktivierte Authentifizierung
            self._client = OpenHABClient(url=base_url)

        self._items = Items(self._client)
        logger.debug(f"OpenHABAPI initialisiert: {base_url}")

    # ──────────────────────────────────────────────────────
    # Basis-Operationen
    # ──────────────────────────────────────────────────────

    def get_state(self, item_name: str) -> Optional[str]:
        """
        Liest den aktuellen State eines Items.
        Gibt den State als String zurück (z.B. "ON", "75", "120.0,100.0,50.0")
        oder None bei Fehler.
        """
        try:
            result = self._items.getItemState(item_name)
            # getItemState gibt dict zurück: {"state": "ON", ...} oder direkt den State-String
            if isinstance(result, dict):
                state = result.get("state") or result.get("value")
            else:
                state = str(result) if result is not None else None

            if state and state != "NULL" and state != "UNDEF":
                return state.strip()
            return None
        except Exception as e:
            logger.error(f"get_state({item_name}): {e}")
            return None

    def send_command(self, item_name: str, command: str) -> bool:
        """
        Sendet einen Befehl an ein Item (POST /items/{name}).
        Gibt True zurück bei Erfolg.
        """
        try:
            self._items.sendCommand(item_name, command)
            logger.debug(f"→ {item_name}: {command}")
            return True
        except Exception as e:
            logger.error(f"send_command({item_name}, {command}): {e}")
            return False

    def post_update(self, item_name: str, state: str) -> bool:
        """
        Setzt den State eines Items direkt (PUT /items/{name}/state).
        Für State-Restore geeignet: setzt den Wert ohne Befehl auszulösen.
        """
        try:
            self._items.postUpdate(item_name, state)
            logger.debug(f"← {item_name} = {state}")
            return True
        except Exception as e:
            logger.error(f"post_update({item_name}, {state}): {e}")
            return False

    def test_connection(self) -> bool:
        """Prüft ob openHAB erreichbar ist (liest alle Items)."""
        try:
            result = self._items.getAllItems()
            return result is not None
        except Exception as e:
            logger.warning(f"test_connection fehlgeschlagen: {e}")
            return False

    # ──────────────────────────────────────────────────────
    # High-Level Switch / Dimmer
    # ──────────────────────────────────────────────────────

    def toggle_switch(self, item_name: str) -> bool:
        """
        Toggelt einen Switch (ON↔OFF) oder Dimmer (0↔100).
        Liest aktuellen State und sendet das Gegenteil.
        """
        state = self.get_state(item_name)
        if state is None:
            # Fallback: ON senden wenn State unbekannt
            return self.send_command(item_name, "ON")

        # Dimmer: State ist eine Zahl
        try:
            value = float(state)
            new_cmd = "ON" if value == 0.0 else "OFF"
        except ValueError:
            # Switch: State ist ON oder OFF
            new_cmd = "OFF" if state.upper() == "ON" else "ON"

        return self.send_command(item_name, new_cmd)

    def dim_step(self, item_name: str, step: int,
                 dim_min: int = 0, dim_max: int = 100) -> Optional[int]:
        """
        Ändert einen Dimmer um `step` Prozent.
        Positiv = heller, negativ = dunkler.
        Gibt den neuen Wert (0–100) zurück oder None bei Fehler.
        Klemmt automatisch auf [dim_min, dim_max].
        """
        state = self.get_state(item_name)
        if state is None:
            return None

        try:
            current = float(state)
        except ValueError:
            current = 100.0 if state.upper() == "ON" else 0.0

        new_val = max(dim_min, min(dim_max, int(round(current)) + step))
        if self.send_command(item_name, str(new_val)):
            return new_val
        return None

    # ──────────────────────────────────────────────────────
    # High-Level Color (HSB)
    # ──────────────────────────────────────────────────────

    def set_color_by_name(self, item_name: str, color_name: str,
                          colors_dict: dict) -> bool:
        """
        Setzt eine Farbe anhand eines Namens aus dem COLORS-Dict.
        colors_dict: {"rot": "0,100,100", "blau": "240,100,100", ...}
        """
        hsb = colors_dict.get(color_name)
        if not hsb:
            logger.error(f"Farbe '{color_name}' nicht in colors_dict. "
                         f"Verfügbar: {list(colors_dict.keys())}")
            return False
        return self.send_command(item_name, hsb)

    def get_color_hsb(self, item_name: str) -> Optional[tuple]:
        """
        Liest den HSB-State eines Color-Items.
        Gibt (h, s, b) als Float-Tuple zurück oder None.
        Beispiel: ("120.0,100.0,80.0") → (120.0, 100.0, 80.0)
        """
        state = self.get_state(item_name)
        if not state:
            return None
        try:
            parts = state.split(",")
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        except (ValueError, IndexError):
            return None

    def cycle_color(self, item_name: str, cycle_order: list,
                    colors_dict: dict, current_index: int = 0) -> tuple:
        """
        Setzt die nächste Farbe im Zyklus.
        Gibt (neuer_farb_name, neuer_index) zurück.

        cycle_order: Liste von Farbnamen, z.B. ["rot", "gruen", "blau"]
        current_index: aktueller Index im Zyklus (wird von außen getrackt)
        """
        next_index = (current_index + 1) % len(cycle_order)
        next_color = cycle_order[next_index]
        self.set_color_by_name(item_name, next_color, colors_dict)
        return next_color, next_index

    # ──────────────────────────────────────────────────────
    # High-Level Rollershutter
    # ──────────────────────────────────────────────────────

    def rollershutter_toggle(self, item_name: str) -> bool:
        """
        Toggelt Rollladen: UP wenn Position >= 50, sonst DOWN.
        State ist Position 0–100 (0 = offen, 100 = geschlossen).
        """
        state = self.get_state(item_name)
        try:
            pos = float(state) if state else 50.0
            cmd = "DOWN" if pos < 50.0 else "UP"
        except ValueError:
            cmd = "UP"
        return self.send_command(item_name, cmd)

    # ──────────────────────────────────────────────────────
    # High-Level Player
    # ──────────────────────────────────────────────────────

    def player_toggle_play(self, item_name: str) -> bool:
        """
        Toggelt PLAY/PAUSE je nach aktuellem State.
        """
        state = self.get_state(item_name)
        cmd = "PAUSE" if (state and state.upper() == "PLAY") else "PLAY"
        return self.send_command(item_name, cmd)

    def player_command(self, item_name: str, cmd: str) -> bool:
        """
        Sendet einen Player-Befehl.
        Gültig: PLAY, PAUSE, NEXT, PREVIOUS, REWIND, FASTFORWARD
        """
        valid = {"PLAY", "PAUSE", "NEXT", "PREVIOUS", "REWIND", "FASTFORWARD"}
        cmd_upper = cmd.upper()
        if cmd_upper not in valid:
            logger.error(f"Ungültiger Player-Befehl: {cmd}. Gültig: {valid}")
            return False
        return self.send_command(item_name, cmd_upper)

    # ──────────────────────────────────────────────────────
    # State-Snapshot (für State-Save / State-Restore)
    # ──────────────────────────────────────────────────────

    def get_states_snapshot(self, items_dict: dict) -> dict:
        """
        Liest die Zustände aller Items in items_dict und gibt
        einen Snapshot zurück.

        items_dict: {"interner_name": "openHAB_Item_Name"}
        Rückgabe:   {"interner_name": "state_string"}

        Nur Items mit gültigem State (nicht NULL/UNDEF) werden aufgenommen.
        """
        snapshot = {}
        for internal_name, item_name in items_dict.items():
            state = self.get_state(item_name)
            if state is not None:
                snapshot[internal_name] = state
                logger.debug(f"  Snapshot: {internal_name} ({item_name}) = {state}")
            else:
                logger.debug(f"  Snapshot: {internal_name} ({item_name}) = übersprungen (NULL/UNDEF)")
        return snapshot

    def restore_states_snapshot(self, snapshot: dict, items_dict: dict,
                                use_post_update: bool = True) -> int:
        """
        Stellt Zustände aus einem Snapshot wieder her.

        use_post_update=True  → postUpdate (State direkt setzen, kein Befehl)
        use_post_update=False → sendCommand (Item-Befehl senden)

        Gibt Anzahl erfolgreich wiederhergestellter Items zurück.
        """
        restored = 0
        for internal_name, state in snapshot.items():
            item_name = items_dict.get(internal_name)
            if not item_name:
                logger.warning(f"  restore: '{internal_name}' nicht in items_dict")
                continue

            if use_post_update:
                ok = self.post_update(item_name, state)
            else:
                ok = self.send_command(item_name, state)

            if ok:
                restored += 1
                logger.debug(f"  Restored: {item_name} = {state}")
            else:
                logger.warning(f"  Restore fehlgeschlagen: {item_name} = {state}")

        return restored

    # ──────────────────────────────────────────────────────
    # Szenen
    # ──────────────────────────────────────────────────────

    def activate_scene(self, scene_name: str, scenes_dict: dict,
                       all_items_dict: dict) -> int:
        """
        Aktiviert eine Szene: setzt alle Items der Szene auf ihre Werte.

        scenes_dict:    {"scene_film": {"wohnzimmer_dimmer": "20", ...}}
        all_items_dict: {"wohnzimmer_dimmer": "Dimmer_Wohnzimmer", ...}

        Gibt Anzahl gesendeter Befehle zurück.
        """
        if scene_name not in scenes_dict:
            logger.error(f"Szene '{scene_name}' nicht gefunden. "
                         f"Verfügbar: {list(scenes_dict.keys())}")
            return 0

        scene = scenes_dict[scene_name]
        sent = 0
        for internal_name, value in scene.items():
            item_name = all_items_dict.get(internal_name)
            if not item_name:
                logger.warning(f"  Szene '{scene_name}': "
                               f"'{internal_name}' nicht in items_dict")
                continue
            if self.send_command(item_name, value):
                sent += 1
                logger.debug(f"  Szene '{scene_name}': {item_name} = {value}")

        return sent
