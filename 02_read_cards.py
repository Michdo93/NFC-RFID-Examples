#!/usr/bin/env python3
"""
02_read_cards.py – Karten lesen & Diagnose
==========================================
Liest und zeigt an:
  - UID der Karte
  - Karten-Typ (MIFARE / NTAG)
  - Beschriebene Aktions-Daten (wenn vorhanden)
  - Rohdaten (optional)
  - Abgleich mit config.py

Verwendung:
  python3 02_read_cards.py              → Dauerhaft lesen bis Ctrl+C
  python3 02_read_cards.py --raw        → Rohdaten anzeigen
  python3 02_read_cards.py --once       → Nur einmal lesen
  python3 02_read_cards.py --dump       → Kompletten Speicher ausgeben
"""

import time
import argparse
import logging

from acr122u import ACR122U, bytes_to_string
from config import CARD_ACTIONS

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

MAGIC_BYTE = 0xAB
SECTOR_DATA = 1
SECTOR_ID   = 2


def decode_mifare_card(reader: ACR122U) -> dict:
    """
    Liest und dekodiert Daten von einer MIFARE Classic Karte.
    Gibt dict mit uid, action, label, card_id zurück.
    """
    uid_bytes = reader._get_uid_bytes()
    uid_str = reader.get_uid()

    result = {
        "uid": uid_str,
        "type": "MIFARE Classic 1K",
        "action": None,
        "label": None,
        "card_id": None,
        "is_written": False,
        "raw_s1": None,
        "raw_s2": None,
    }

    # Sektor 1 lesen
    s1 = reader.mifare_read_sector(SECTOR_DATA, uid_bytes)
    if s1 is None:
        result["error"] = "Sektor 1 nicht lesbar (Auth-Fehler?)"
        return result

    result["raw_s1"] = s1

    # Magic-Byte prüfen
    if s1[0] != MAGIC_BYTE:
        result["is_written"] = False
        return result

    result["is_written"] = True

    # Aktions-String decodieren
    action_len = (s1[1] << 8) | s1[2]
    action_bytes = s1[3:3+action_len]
    result["action"] = bytes(action_bytes).decode("utf-8", errors="ignore")

    # Label decodieren
    label_bytes = s1[35:47]
    result["label"] = bytes_to_string(label_bytes)

    # Sektor 2 (Karten-ID)
    s2 = reader.mifare_read_block(SECTOR_ID * 4, uid_bytes)
    if s2:
        result["raw_s2"] = s2
        result["card_id"] = bytes_to_string(s2)

    return result


def decode_ntag_card(reader: ACR122U) -> dict:
    """
    Liest und dekodiert Daten von einer NTAG-Karte.
    """
    uid_str = reader.get_uid()
    result = {
        "uid": uid_str,
        "type": "NTAG (NFC)",
        "action": None,
        "label": None,
        "card_id": None,
        "is_written": False,
        "raw_pages": [],
    }

    # Rohdaten lesen
    all_bytes = []
    for page in range(4, 50):
        data = reader.ntag_read_page(page)
        if data is None:
            break
        # Endmarkierung
        if data[:2] == [0xFF, 0xFF]:
            break
        all_bytes.extend(data)
        result["raw_pages"].append((page, data))

    if not all_bytes:
        return result

    # Dekodieren
    try:
        payload = bytes(all_bytes).replace(b"\x00", b"").decode("utf-8", errors="ignore")
        if "|" in payload and payload.startswith(f"{MAGIC_BYTE:02X}"):
            result["is_written"] = True
            parts = payload.split("|")
            if len(parts) >= 4:
                result["action"] = parts[1]
                result["label"] = parts[2]
                result["card_id"] = parts[3]
    except Exception:
        pass

    return result


def try_decode_card(reader: ACR122U) -> dict:
    """
    Versucht automatisch den Karten-Typ zu erkennen und zu dekodieren.
    """
    uid_bytes = reader._get_uid_bytes()
    if uid_bytes is None:
        return {"uid": None, "error": "Keine UID lesbar"}

    # Versuche MIFARE
    s1 = reader.mifare_read_sector(SECTOR_DATA, uid_bytes)
    if s1 is not None:
        return decode_mifare_card(reader)

    # Versuche NTAG
    page = reader.ntag_read_page(4)
    if page is not None:
        return decode_ntag_card(reader)

    return {
        "uid": reader.get_uid(),
        "type": "Unbekannt",
        "is_written": False,
        "error": "Karten-Typ nicht erkannt"
    }


def print_card_info(info: dict, show_raw: bool = False):
    """Gibt Karten-Info formatiert aus."""
    print("\n" + "─"*50)
    uid = info.get("uid", "N/A")
    print(f"  UID:       {uid}")
    print(f"  Typ:       {info.get('type', 'Unbekannt')}")

    if info.get("error"):
        print(f"  ✗ Fehler:  {info['error']}")
        return

    if info.get("is_written"):
        print(f"  Aktion:    {info.get('action', 'N/A')}")
        print(f"  Label:     {info.get('label', 'N/A')}")
        print(f"  Karten-ID: {info.get('card_id', 'N/A')}")

        # Abgleich mit config.py
        if uid in CARD_ACTIONS:
            cfg = CARD_ACTIONS[uid]
            print(f"\n  ✓ In config.py: {cfg['label']}")
            if cfg["action"] != info.get("action"):
                print(f"  ⚠ Abweichung! config.py: {cfg['action']}")
                print(f"               Karte:     {info.get('action')}")
        else:
            print(f"\n  ⚠ UID nicht in config.py eingetragen!")
            if info.get("action"):
                print(f"    → Füge in config.py hinzu:")
                print(f'      "{uid}": {{')
                print(f'          "label":  "{info.get("label", "")}",')
                print(f'          "action": "{info.get("action", "")}",')
                print(f'      }},')
    else:
        print("  Status:    Nicht beschrieben (leer)")

    if show_raw:
        print("\n  Rohdaten:")
        if info.get("raw_s1"):
            s1 = info["raw_s1"]
            for i in range(0, len(s1), 16):
                chunk = s1[i:i+16]
                hex_str = " ".join(f"{b:02X}" for b in chunk)
                print(f"    S1[{i//16}]: {hex_str}")
        if info.get("raw_s2"):
            hex_str = " ".join(f"{b:02X}" for b in info["raw_s2"])
            print(f"    S2[0]:  {hex_str}")
        if info.get("raw_pages"):
            for page, data in info["raw_pages"]:
                hex_str = " ".join(f"{b:02X}" for b in data)
                print(f"    P[{page:02d}]: {hex_str}")


def dump_mifare(reader: ACR122U):
    """Gibt den kompletten MIFARE Classic Speicher aus (alle Sektoren)."""
    uid_bytes = reader._get_uid_bytes()
    uid_str = reader.get_uid()
    print(f"\n  MIFARE Classic Dump – UID: {uid_str}")
    print("  " + "─"*46)

    for sector in range(16):  # 16 Sektoren × 4 Blöcke = 64 Blöcke
        start_block = sector * 4
        print(f"\n  Sektor {sector:2d}:")
        for block in range(start_block, start_block + 4):
            is_trailer = (block % 4 == 3)
            data = reader.mifare_read_block(block, uid_bytes)
            if data:
                hex_str = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                trailer_mark = " [TRAILER]" if is_trailer else ""
                print(f"    Block {block:2d}: {hex_str}  |{ascii_str}|{trailer_mark}")
            else:
                print(f"    Block {block:2d}: <nicht lesbar>")


def dump_ntag(reader: ACR122U):
    """Gibt alle NTAG-Seiten aus."""
    uid_str = reader.get_uid()
    print(f"\n  NTAG Dump – UID: {uid_str}")
    print("  " + "─"*46)

    for page in range(0, 50):
        data = reader.ntag_read_page(page)
        if data is None:
            print(f"  Ende bei Seite {page}")
            break
        hex_str = " ".join(f"{b:02X}" for b in data)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        print(f"    Seite {page:2d}: {hex_str}  |{ascii_str}|")


def continuous_read(show_raw: bool = False, dump: bool = False):
    """Dauerhafter Lese-Modus."""
    print("\n" + "═"*50)
    print("   RFID/NFC Karten-Leser")
    print("   (Ctrl+C zum Beenden)")
    print("═"*50)
    print("\n>>> Lege eine Karte auf den Reader...")

    reader = ACR122U()
    last_uid = None

    try:
        while True:
            uid = reader.wait_for_card(timeout=3.0)

            if uid and uid != last_uid:
                last_uid = uid
                info = try_decode_card(reader)
                print_card_info(info, show_raw)

                if dump:
                    uid_bytes = reader._get_uid_bytes()
                    s1 = reader.mifare_read_sector(1, uid_bytes)
                    if s1 is not None:
                        dump_mifare(reader)
                    else:
                        dump_ntag(reader)

                # Warte bis Karte entfernt
                reader.wait_for_removal(timeout=30.0)
                reader.disconnect()
                last_uid = None
                print("\n  (Karte entfernt, warte auf nächste...)")

            elif not uid:
                last_uid = None
                reader.disconnect()
                time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n\nBeendet.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID/NFC Karten lesen und anzeigen")
    parser.add_argument("--raw", action="store_true",
                        help="Rohdaten anzeigen")
    parser.add_argument("--once", action="store_true",
                        help="Nur einmal lesen und beenden")
    parser.add_argument("--dump", action="store_true",
                        help="Kompletten Speicher ausgeben")
    args = parser.parse_args()

    if args.once:
        reader = ACR122U()
        print(">>> Lege eine Karte auf (max. 30s)...")
        uid = reader.wait_for_card(timeout=30.0)
        if uid:
            info = try_decode_card(reader)
            print_card_info(info, args.raw)
            if args.dump:
                uid_bytes = reader._get_uid_bytes()
                s1 = reader.mifare_read_sector(1, uid_bytes)
                if s1 is not None:
                    dump_mifare(reader)
                else:
                    dump_ntag(reader)
        else:
            print("✗ Keine Karte erkannt.")
        reader.disconnect()
    else:
        continuous_read(show_raw=args.raw, dump=args.dump)
