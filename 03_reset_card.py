#!/usr/bin/env python3
"""
03_reset_card.py – Karte zurücksetzen oder überschreiben
=========================================================
Setzt eine MIFARE Classic oder NTAG Karte auf Werkseinstellungen zurück
(löscht die beschriebenen Daten).

Verwendung:
  python3 03_reset_card.py           → Interaktiv eine Karte zurücksetzen
  python3 03_reset_card.py --all     → Alle aufgelegten Karten nacheinander zurücksetzen
  python3 03_reset_card.py --verify  → Nach Reset Karte verifizieren
"""

import time
import argparse
import logging

from acr122u import ACR122U, bytes_to_string

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

MAGIC_BYTE   = 0xAB
SECTOR_DATA  = 1
SECTOR_ID    = 2
EMPTY_BLOCK  = [0x00] * 16
EMPTY_SECTOR = [0x00] * 48


def reset_mifare_card(reader: ACR122U) -> bool:
    """
    Setzt die beschriebenen Sektoren einer MIFARE Classic Karte zurück.
    Überschreibt Sektor 1 und 2 mit Nullen.
    ACHTUNG: Sektor 0 (Hersteller-Info) und Trailer-Blöcke werden NICHT gelöscht.
    """
    uid_bytes = reader._get_uid_bytes()
    if uid_bytes is None:
        print("  ✗ UID nicht lesbar")
        return False

    print("  → Lösche Sektor 1 (Aktions-Daten)...")
    if not reader.mifare_write_sector(SECTOR_DATA, EMPTY_SECTOR, uid_bytes):
        print("  ✗ Sektor 1 konnte nicht gelöscht werden")
        return False

    print("  → Lösche Sektor 2 (Karten-ID)...")
    if not reader.mifare_write_block(SECTOR_ID * 4, EMPTY_BLOCK, uid_bytes):
        print("  ✗ Block 8 konnte nicht gelöscht werden")
        return False

    return True


def reset_ntag_card(reader: ACR122U) -> bool:
    """
    Setzt die Daten-Seiten einer NTAG-Karte zurück.
    Löscht Seiten 4 bis 44.
    """
    print("  → Lösche NTAG Daten-Seiten 4-44...")
    empty_page = [0x00, 0x00, 0x00, 0x00]
    errors = 0

    for page in range(4, 45):
        if not reader.ntag_write_page(page, empty_page):
            errors += 1
            if errors > 3:
                print(f"  ✗ Zu viele Fehler beim Löschen (bei Seite {page})")
                return False
        # Kurze Pause zwischen Schreib-Operationen
        if page % 10 == 0:
            time.sleep(0.05)

    return True


def verify_reset(reader: ACR122U) -> bool:
    """Prüft ob die Karte wirklich leer ist."""
    uid_bytes = reader._get_uid_bytes()

    # MIFARE-Prüfung
    s1 = reader.mifare_read_sector(SECTOR_DATA, uid_bytes)
    if s1 is not None:
        if s1[0] == MAGIC_BYTE:
            print("  ✗ Verifikation fehlgeschlagen: Magic-Byte noch vorhanden!")
            return False
        if any(b != 0 for b in s1):
            print("  ⚠ Sektor 1 nicht komplett leer, aber Magic-Byte fehlt → OK")
        return True

    # NTAG-Prüfung
    page = reader.ntag_read_page(4)
    if page is not None:
        if any(b != 0 for b in page):
            print("  ✗ Verifikation fehlgeschlagen: Seite 4 nicht leer!")
            return False
        return True

    return True  # Wenn nichts gelesen werden kann → als OK werten


def detect_and_reset(reader: ACR122U, verify: bool = False) -> bool:
    """
    Erkennt Karten-Typ automatisch und setzt entsprechend zurück.
    """
    uid_str = reader.get_uid()
    uid_bytes = reader._get_uid_bytes()
    print(f"  UID: {uid_str}")

    # Typ erkennen
    success = False
    s1 = reader.mifare_read_sector(SECTOR_DATA, uid_bytes)
    if s1 is not None:
        print("  Typ: MIFARE Classic 1K")
        if s1[0] == MAGIC_BYTE:
            print(f"  Karte ist beschrieben (Aktion gefunden)")
        else:
            print("  Karte ist bereits leer / nicht beschrieben")
            if input("  Trotzdem zurücksetzen? (j/n): ").strip().lower() != "j":
                return True
        success = reset_mifare_card(reader)
    else:
        page4 = reader.ntag_read_page(4)
        if page4 is not None:
            print("  Typ: NTAG (NFC)")
            success = reset_ntag_card(reader)
        else:
            print("  ✗ Karten-Typ nicht erkannt")
            return False

    if success and verify:
        print("  → Verifiziere...")
        time.sleep(0.2)
        if verify_reset(reader):
            print("  ✓ Verifikation OK")
        else:
            success = False

    return success


def interactive_reset(verify: bool = False):
    """Interaktiver Rücksetz-Modus."""
    print("\n" + "═"*50)
    print("   RFID/NFC Karte zurücksetzen")
    print("═"*50)
    print("\n  ⚠ WARNUNG: Die Daten auf der Karte werden gelöscht!")
    print("  Die Karten-UID (fest eingebrannt) bleibt erhalten.\n")

    print(">>> Lege die Karte auf den Reader...")
    reader = ACR122U()
    uid = reader.wait_for_card(timeout=30.0)

    if uid is None:
        print("✗ Timeout: Keine Karte erkannt.")
        return

    print(f"\n  Karte erkannt: {uid}")
    confirm = input("  Karte zurücksetzen? (ja/nein): ").strip().lower()
    if confirm != "ja":
        print("  Abgebrochen.")
        reader.disconnect()
        return

    success = detect_and_reset(reader, verify)

    if success:
        print(f"\n  ✓ Karte erfolgreich zurückgesetzt!")
    else:
        print(f"\n  ✗ Zurücksetzen fehlgeschlagen!")

    reader.disconnect()


def batch_reset(verify: bool = False):
    """Setzt nacheinander alle aufgelegten Karten zurück."""
    print("\n" + "═"*50)
    print("   Batch-Rücksetzung")
    print("   (Ctrl+C zum Beenden)")
    print("═"*50)
    print("\n  ⚠ WARNUNG: Alle aufgelegten Karten werden gelöscht!")
    print("  Drücke ENTER zum Starten oder Ctrl+C zum Abbruch...")
    input()

    reader = ACR122U()
    count = 0

    try:
        while True:
            print(f"\n>>> Lege Karte #{count+1} auf (Ctrl+C wenn fertig)...")
            uid = reader.wait_for_card(timeout=60.0)
            if uid is None:
                continue

            print(f"  Karte erkannt: {uid}")
            success = detect_and_reset(reader, verify)
            if success:
                print(f"  ✓ Karte #{count+1} zurückgesetzt")
                count += 1
            else:
                print(f"  ✗ Fehler bei Karte #{count+1}")

            reader.wait_for_removal(timeout=10.0)
            reader.disconnect()
            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n\n✓ {count} Karte(n) zurückgesetzt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID/NFC Karten zurücksetzen")
    parser.add_argument("--all", action="store_true",
                        help="Batch-Modus: Karten nacheinander zurücksetzen")
    parser.add_argument("--verify", action="store_true",
                        help="Nach Reset verifizieren")
    args = parser.parse_args()

    if args.all:
        batch_reset(verify=args.verify)
    else:
        interactive_reset(verify=args.verify)
