#!/usr/bin/env python3
"""
01_write_cards.py – Karten beschreiben (RFID MIFARE / NFC NTAG)
================================================================
Schreibt Aktions-Metadaten auf eine Karte.

Auf MIFARE Classic 1K (RFID):
  - Sektor 1, Block 4-6: Aktions-String + Label (max. 46 Zeichen)
  - Sektor 2, Block 8-10: Karten-ID (max. 16 Zeichen)

Auf NTAG215 (NFC):
  - Seiten 4-N: Aktions-String (NDEF-ähnlich, vereinfacht)

Verwendung:
  python3 01_write_cards.py
  → Interaktives Menü

  python3 01_write_cards.py --uid-only
  → Zeigt nur die UID der aufgelegten Karte
"""

import sys
import time
import argparse
import logging

from acr122u import ACR122U, string_to_bytes_padded, bytes_to_string
from config import CARD_ACTIONS, COLORS, SCENES

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# ──────────────────────────────────────────────────────────────────────
# Datenformat auf der RFID-Karte (MIFARE Classic 1K)
# ──────────────────────────────────────────────────────────────────────
# Sektor 1 (Block 4, 5, 6) → 48 Bytes gesamt:
#   Bytes  0     : Magic-Byte 0xAB (markiert eine beschriebene Karte)
#   Bytes  1-2   : Länge des Aktions-Strings (2 Bytes, big-endian)
#   Bytes  3-34  : Aktions-String (max. 32 Bytes, UTF-8)
#   Bytes 35-46  : Label (max. 12 Bytes, UTF-8)
#   Bytes 47     : Reserviert
#
# Sektor 2 (Block 8) → 16 Bytes:
#   Bytes  0-15  : Karten-ID (max. 16 Bytes, UTF-8)
# ──────────────────────────────────────────────────────────────────────

MAGIC_BYTE = 0xAB
SECTOR_DATA = 1    # Aktions-Sektor
SECTOR_ID   = 2    # ID-Sektor
MAX_ACTION_LEN = 32
MAX_LABEL_LEN  = 12
MAX_ID_LEN     = 16


def encode_card_data(action: str, label: str, card_id: str) -> tuple:
    """
    Kodiert Karten-Daten in Byte-Arrays für Sektor 1 und 2.
    Gibt (data_sector1_48bytes, data_sector2_16bytes) zurück.
    """
    action_bytes = action.encode("utf-8")[:MAX_ACTION_LEN]
    action_len = len(action_bytes)
    label_bytes = label.encode("utf-8")[:MAX_LABEL_LEN]
    id_bytes = card_id.encode("utf-8")[:MAX_ID_LEN]

    # Sektor 1: 48 Bytes
    s1 = [0x00] * 48
    s1[0] = MAGIC_BYTE
    s1[1] = (action_len >> 8) & 0xFF
    s1[2] = action_len & 0xFF
    for i, b in enumerate(action_bytes):
        s1[3 + i] = b
    for i, b in enumerate(label_bytes):
        s1[35 + i] = b

    # Sektor 2: 16 Bytes (nur Block 8)
    s2 = [0x00] * 16
    for i, b in enumerate(id_bytes):
        s2[i] = b

    return s1, s2


def write_rfid_card(reader: ACR122U, action: str, label: str, card_id: str) -> bool:
    """Schreibt Daten auf eine MIFARE Classic 1K Karte."""
    uid = reader._get_uid_bytes()
    if uid is None:
        print("  ✗ UID konnte nicht gelesen werden.")
        return False

    s1, s2 = encode_card_data(action, label, card_id)

    print(f"  → Schreibe Sektor {SECTOR_DATA} (Aktions-Daten)...")
    if not reader.mifare_write_sector(SECTOR_DATA, s1, uid):
        print("  ✗ Fehler beim Schreiben von Sektor 1")
        return False

    print(f"  → Schreibe Sektor {SECTOR_ID} (Karten-ID)...")
    # Nur Block 8 (erster Block von Sektor 2)
    if not reader.mifare_write_block(SECTOR_ID * 4, s2, uid):
        print("  ✗ Fehler beim Schreiben von Sektor 2")
        return False

    return True


def write_nfc_card(reader: ACR122U, action: str, label: str, card_id: str) -> bool:
    """
    Schreibt Daten auf eine NTAG-Karte (NFC).
    Format: Vereinfachte Textstruktur, Seite 4+
    """
    payload = f"{MAGIC_BYTE:02X}|{action}|{label}|{card_id}"
    payload_bytes = payload.encode("utf-8")

    # 4 Bytes pro Seite, ab Seite 4
    pages_needed = (len(payload_bytes) + 3) // 4
    print(f"  → Benötige {pages_needed} Seiten für {len(payload_bytes)} Bytes")

    for i in range(pages_needed):
        chunk = payload_bytes[i*4:(i+1)*4]
        # Auf 4 Bytes auffüllen
        chunk_padded = list(chunk) + [0x00] * (4 - len(chunk))
        page = 4 + i
        if not reader.ntag_write_page(page, chunk_padded):
            print(f"  ✗ Fehler beim Schreiben von Seite {page}")
            return False

    # Endmarkierung
    end_page = 4 + pages_needed
    try:
        reader.ntag_write_page(end_page, [0xFF, 0xFF, 0x00, 0x00])
    except Exception:
        pass

    return True


def detect_card_type(reader: ACR122U) -> str:
    """
    Erkennt Karten-Typ: 'MIFARE', 'NTAG', oder 'UNKNOWN'.
    Methode: Versucht MIFARE-Authentifizierung.
    """
    uid = reader._get_uid_bytes()
    if uid is None:
        return "UNKNOWN"

    # Versuche MIFARE-Authentifizierung an Block 4
    try:
        data = reader.mifare_read_block(4, uid)
        if data is not None:
            return "MIFARE"
    except Exception:
        pass

    # Versuche NTAG-Lesen
    try:
        data = reader.ntag_read_page(4)
        if data is not None:
            return "NTAG"
    except Exception:
        pass

    return "UNKNOWN"


# ──────────────────────────────────────────────────────────────────────
# Interaktives Menü
# ──────────────────────────────────────────────────────────────────────

def show_action_menu() -> str:
    """Zeigt Aktions-Auswahl und gibt den gewählten Aktions-String zurück."""
    print("\n╔══════════════════════════════════════════╗")
    print("║           AKTION AUSWÄHLEN               ║")
    print("╠══════════════════════════════════════════╣")
    print("║  1) toggle:<item>                        ║")
    print("║  2) dim_up:<item>                        ║")
    print("║  3) dim_down:<item>                      ║")
    print("║  4) color:<item>:<farbe>                 ║")
    print("║  5) color_cycle:<item>                   ║")
    print("║  6) scene:<name>                         ║")
    print("║  7) state_save                           ║")
    print("║  8) state_restore:<slot>                 ║")
    print("║  9) game_toggle                          ║")
    print("║ 10) rollershutter_toggle:<item>          ║")
    print("║ 11) rollershutter_up:<item>              ║")
    print("║ 12) rollershutter_down:<item>            ║")
    print("║ 13) player:<item>:<befehl>               ║")
    print("║ 14) Eigene Aktion eingeben               ║")
    print("╚══════════════════════════════════════════╝")

    available_colors = ", ".join(COLORS.keys())
    available_scenes = ", ".join(SCENES.keys())
    player_commands = "PLAY, PAUSE, NEXT, PREVIOUS, REWIND, FASTFORWARD"

    choice = input("\nAuswahl (1-14): ").strip()

    if choice == "1":
        item = input("Item-Name (aus config.py): ").strip()
        return f"toggle:{item}"
    elif choice == "2":
        item = input("Dimmer-Item-Name: ").strip()
        return f"dim_up:{item}"
    elif choice == "3":
        item = input("Dimmer-Item-Name: ").strip()
        return f"dim_down:{item}"
    elif choice == "4":
        item = input("Color-Item-Name: ").strip()
        print(f"Verfügbare Farben: {available_colors}")
        color = input("Farbe: ").strip()
        return f"color:{item}:{color}"
    elif choice == "5":
        item = input("Color-Item-Name: ").strip()
        return f"color_cycle:{item}"
    elif choice == "6":
        print(f"Verfügbare Szenen: {available_scenes}")
        scene = input("Szenen-Name: ").strip()
        return f"scene:{scene}"
    elif choice == "7":
        return "state_save"
    elif choice == "8":
        slot = input("Slot-Name (z.B. memory_slot_1): ").strip()
        return f"state_restore:{slot}"
    elif choice == "9":
        return "game_toggle"
    elif choice == "10":
        item = input("Rollladen-Item-Name: ").strip()
        return f"rollershutter_toggle:{item}"
    elif choice == "11":
        item = input("Rollladen-Item-Name: ").strip()
        return f"rollershutter_up:{item}"
    elif choice == "12":
        item = input("Rollladen-Item-Name: ").strip()
        return f"rollershutter_down:{item}"
    elif choice == "13":
        item = input("Player-Item-Name: ").strip()
        print(f"Befehle: {player_commands}")
        cmd = input("Befehl: ").strip().upper()
        return f"player:{item}:{cmd}"
    elif choice == "14":
        return input("Eigene Aktion eingeben: ").strip()
    else:
        print("Ungültige Auswahl, versuche erneut.")
        return show_action_menu()


def interactive_write():
    """Interaktiver Schreib-Modus."""
    print("\n" + "═"*50)
    print("   RFID/NFC Karten-Schreiber")
    print("═"*50)

    # Aktion auswählen
    action = show_action_menu()
    label = input("\nLabel/Bezeichnung (z.B. 'Wohnzimmer Licht'): ").strip()
    card_id = input("Karten-ID (z.B. 'wz_licht_01', eindeutig): ").strip()

    if not card_id:
        card_id = action[:16]

    print(f"\n  Aktion:    {action}")
    print(f"  Label:     {label}")
    print(f"  Karten-ID: {card_id}")
    print("\n>>> Lege jetzt die Karte auf den Reader...")

    reader = ACR122U()
    uid = reader.wait_for_card(timeout=30.0)
    if uid is None:
        print("✗ Timeout: Keine Karte erkannt.")
        return

    print(f"  ✓ Karte erkannt: UID = {uid}")

    # Karten-Typ erkennen
    card_type = detect_card_type(reader)
    print(f"  Karten-Typ: {card_type}")

    print(f"\n  Schreibe auf {card_type}-Karte...")
    success = False

    if card_type == "MIFARE":
        success = write_rfid_card(reader, action, label, card_id)
    elif card_type == "NTAG":
        success = write_nfc_card(reader, action, label, card_id)
    else:
        print("  ✗ Unbekannter Karten-Typ! Versuche MIFARE...")
        success = write_rfid_card(reader, action, label, card_id)

    if success:
        print(f"\n  ✓ Karte erfolgreich beschrieben!")
        print(f"    UID:    {uid}")
        print(f"    Aktion: {action}")
        print(f"    Label:  {label}")
        print(f"\n  → Trage diese UID jetzt in config.py ein:")
        print(f'    "{uid}": {{')
        print(f'        "label":  "{label}",')
        print(f'        "action": "{action}",')
        print(f'    }},')
    else:
        print("\n  ✗ Schreiben fehlgeschlagen!")

    reader.disconnect()


def batch_write_from_config():
    """
    Schreibt alle Karten aus config.py nacheinander.
    Für jede Karte in CARD_ACTIONS muss die Karte aufgelegt werden.
    """
    print("\n" + "═"*50)
    print("   Batch-Schreiben aus config.py")
    print("═"*50)
    print(f"  {len(CARD_ACTIONS)} Karten definiert\n")

    reader = ACR122U()

    for uid_config, card_info in CARD_ACTIONS.items():
        action = card_info["action"]
        label = card_info["label"]
        card_id = uid_config.replace(":", "")[:16]

        print(f"\n─── Nächste Karte: {label} ───")
        print(f"  Aktion: {action}")
        print(">>> Lege die Karte auf (oder ENTER zum Überspringen)...")

        # Auf Eingabe oder Karte warten
        import select
        uid = reader.wait_for_card(timeout=20.0)
        if uid is None:
            print("  → Übersprungen")
            continue

        card_type = detect_card_type(reader)
        success = False
        if card_type == "MIFARE":
            success = write_rfid_card(reader, action, label, card_id)
        elif card_type == "NTAG":
            success = write_nfc_card(reader, action, label, card_id)

        if success:
            print(f"  ✓ {label} geschrieben (UID: {uid})")
        else:
            print(f"  ✗ Fehler bei {label}")

        reader.disconnect()
        time.sleep(1.0)

    print("\n✓ Batch-Schreiben abgeschlossen")


def uid_only_mode():
    """Zeigt nur die UID der aufgelegten Karte."""
    print(">>> Lege eine Karte auf den Reader (Ctrl+C zum Beenden)...")
    reader = ACR122U()
    try:
        while True:
            uid = reader.wait_for_card(timeout=5.0)
            if uid:
                card_type = detect_card_type(reader)
                print(f"  UID: {uid}  Typ: {card_type}")
                reader.wait_for_removal(timeout=10.0)
                reader.disconnect()
                print("  (Karte entfernt, warte auf nächste...)")
    except KeyboardInterrupt:
        print("\nBeendet.")


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID/NFC Karten beschreiben")
    parser.add_argument("--uid-only", action="store_true",
                        help="Nur UIDs anzeigen, nichts schreiben")
    parser.add_argument("--batch", action="store_true",
                        help="Alle Karten aus config.py schreiben")
    args = parser.parse_args()

    if args.uid_only:
        uid_only_mode()
    elif args.batch:
        batch_write_from_config()
    else:
        while True:
            interactive_write()
            again = input("\nWeitere Karte beschreiben? (j/n): ").strip().lower()
            if again != "j":
                break
        print("Fertig.")
