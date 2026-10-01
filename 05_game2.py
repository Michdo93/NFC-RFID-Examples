#!/usr/bin/env python3
"""
05_game.py – Karten-Memory / Reihenfolge-Spiel
===============================================
Ein einfaches Reaktions- und Gedächtnisspiel mit RFID-Karten.

Spielprinzip: "Karten-Sequenz"
  1. Das System zeigt eine zufällige Farb-Sequenz über die Lampe an
     (z.B. Rot → Blau → Grün)
  2. Der Spieler muss die passenden RFID-Karten in der gleichen
     Reihenfolge auflegen
  3. Richtig → nächste Runde (Sequenz wird länger)
  4. Falsch → Game Over

Spielstart: Karte mit Aktion "game_toggle" auflegen
Abbruch: Gleiche Karte nochmal oder Ctrl+C

openHAB-Integration:
  - Farbwechsel über Color-Item → Spieler sieht die Sequenz an der Lampe
  - Switch_GameWin / Switch_GameFail werden bei Sieg/Niederlage gesetzt

Verwendung:
  python3 05_game.py
  python3 05_game.py --no-write    → Nur Konsolenausgabe, keine openHAB-Befehle
  python3 05_game.py --rounds 5    → Startet mit 5 Items in der Sequenz
"""

import time
import random
import argparse
import logging
from typing import Optional

from acr122u import ACR122U
from openhab_api import OpenHABAPI
from config import (
    OPENHAB_URL, OPENHAB_TOKEN, OPENHAB_USERNAME, OPENHAB_PASSWORD,
    GAME_SEQUENCE, GAME_WIN_ITEM, GAME_FAIL_ITEM, GAME_FEEDBACK_ITEM,
    CARD_ACTIONS, COLORS,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

DRY_RUN = False

# ──────────────────────────────────────────────────────────────────────
# Farb-Feedback über openHAB
# ──────────────────────────────────────────────────────────────────────

def flash_color(api: OpenHABAPI, color_hsb: str, times: int = 3, interval: float = 0.4):
    """Lässt das Feedback-Item `times` mal in der Farbe aufblinken."""
    if DRY_RUN:
        return
    for _ in range(times):
        api.send_command(GAME_FEEDBACK_ITEM, color_hsb)
        time.sleep(interval)
        api.send_command(GAME_FEEDBACK_ITEM, "0,0,0")  # Aus
        time.sleep(interval)


def show_color(api: OpenHABAPI, color_hsb: str, duration: float = 1.0):
    """Zeigt eine Farbe für `duration` Sekunden."""
    if DRY_RUN:
        return
    api.send_command(GAME_FEEDBACK_ITEM, color_hsb)
    time.sleep(duration)
    api.send_command(GAME_FEEDBACK_ITEM, "0,0,0")
    time.sleep(0.3)


def signal_win(api: OpenHABAPI):
    """Sieg-Signal: Grün blinken + openHAB Item setzen."""
    print("  🏆 GEWONNEN!")
    flash_color(api, "120,100,80", times=5, interval=0.3)  # Grün
    if not DRY_RUN:
        api.send_command(GAME_WIN_ITEM, "ON")
        time.sleep(1.0)
        api.send_command(GAME_WIN_ITEM, "OFF")


def signal_fail(api: OpenHABAPI):
    """Niederlage-Signal: Rot blinken + openHAB Item setzen."""
    print("  ❌ FALSCH!")
    flash_color(api, "0,100,100", times=4, interval=0.25)  # Rot
    if not DRY_RUN:
        api.send_command(GAME_FAIL_ITEM, "ON")
        time.sleep(1.0)
        api.send_command(GAME_FAIL_ITEM, "OFF")


def signal_correct(api: OpenHABAPI):
    """Korrekte Karte: kurzes Grün."""
    if not DRY_RUN:
        api.send_command(GAME_FEEDBACK_ITEM, "120,100,60")
        time.sleep(0.3)
        api.send_command(GAME_FEEDBACK_ITEM, "0,0,0")


# ──────────────────────────────────────────────────────────────────────
# Karten-Farb-Mapping
# ──────────────────────────────────────────────────────────────────────

def get_card_color(uid: str) -> Optional[str]:
    """
    Gibt die Farbe (HSB) zurück, die einer Karte zugeordnet ist.
    Liest aus der CARD_ACTIONS-Konfiguration.
    """
    if uid not in CARD_ACTIONS:
        return None
    action = CARD_ACTIONS[uid].get("action", "")
    # Format: color:<item>:<farbe>
    if action.startswith("color:"):
        parts = action.split(":")
        if len(parts) >= 3:
            color_name = parts[2]
            return COLORS.get(color_name)
    return None


def get_card_label(uid: str) -> str:
    """Gibt das Label einer Karte zurück."""
    if uid in CARD_ACTIONS:
        return CARD_ACTIONS[uid].get("label", uid)
    return uid


# ──────────────────────────────────────────────────────────────────────
# Spiellogik
# ──────────────────────────────────────────────────────────────────────

def wait_for_card_scan(reader: ACR122U, timeout: float = 30.0) -> Optional[str]:
    """Wartet auf Karte, gibt UID zurück oder None bei Timeout."""
    uid = reader.wait_for_card(timeout=timeout)
    if uid:
        reader.wait_for_removal(timeout=10.0)
        reader.disconnect()
        time.sleep(0.3)
    return uid


def show_sequence(api: OpenHABAPI, sequence: list):
    """
    Zeigt die aktuelle Sequenz über das Feedback-Item an.
    sequence: Liste von UIDs
    """
    print(f"\n  📺 Sequenz wird gezeigt ({len(sequence)} Karte(n)):")
    time.sleep(0.5)

    for i, uid in enumerate(sequence):
        color_hsb = get_card_color(uid)
        label = get_card_label(uid)
        print(f"    {i+1}. {label}")

        if color_hsb:
            show_color(api, color_hsb, duration=1.0)
        else:
            time.sleep(1.3)

    time.sleep(0.5)
    print("  ─── Jetzt du! ───")


def play_round(api: OpenHABAPI, reader: ACR122U, sequence: list, game_toggle_uid: str) -> bool:
    """
    Spielt eine Runde.
    Gibt True zurück wenn korrekt, False bei Fehler oder Abbruch.
    """
    for i, expected_uid in enumerate(sequence):
        expected_label = get_card_label(expected_uid)
        print(f"\n  Karte {i+1}/{len(sequence)}: Lege auf...")

        uid = wait_for_card_scan(reader, timeout=20.0)

        if uid is None:
            print("  ⏰ Timeout – zu langsam!")
            return False

        if uid == game_toggle_uid:
            print("  🛑 Spiel abgebrochen")
            return None  # Abbruch

        if uid == expected_uid:
            print(f"  ✓ Richtig: {expected_label}")
            signal_correct(api)
        else:
            wrong_label = get_card_label(uid)
            print(f"  ✗ Falsch! Erwartet: {expected_label}, Got: {wrong_label}")
            signal_fail(api)
            return False

    return True


def run_game(api: OpenHABAPI, start_length: int = 1):
    """Haupt-Spielschleife."""
    reader = ACR122U()

    # Welche Karten können im Spiel verwendet werden?
    game_cards = [
        uid for uid in GAME_SEQUENCE
        if uid in CARD_ACTIONS
    ]

    if len(game_cards) < 2:
        print("  ✗ Zu wenige Spielkarten in GAME_SEQUENCE konfiguriert!")
        print("    Mindestens 2 Karten mit color:-Aktionen benötigt.")
        return

    # Game-Toggle Karte finden
    game_toggle_uid = None
    for uid, info in CARD_ACTIONS.items():
        if info.get("action") == "game_toggle":
            game_toggle_uid = uid
            break

    print("\n" + "═"*55)
    print("   🎮 KARTEN-SEQUENZ SPIEL")
    print("═"*55)
    print(f"  Spielkarten: {len(game_cards)}")
    for uid in game_cards:
        print(f"    • {get_card_label(uid)}")
    print()

    score = 0
    level = start_length
    sequence = []

    while True:
        # Sequenz um eine Karte erweitern
        sequence.append(random.choice(game_cards))
        print(f"\n  ══ Level {level} (Sequenz-Länge: {len(sequence)}) ══")

        # Sequenz anzeigen
        show_sequence(api, sequence)

        # Eingabe-Runde
        result = play_round(api, reader, sequence, game_toggle_uid)

        if result is None:
            # Abgebrochen
            print(f"\n  Spiel beendet. Punkte: {score}")
            break
        elif result:
            score += len(sequence) * 10
            print(f"\n  ✓ Korrekt! Punkte: {score}")
            signal_win(api)
            level += 1
            time.sleep(1.5)
        else:
            print(f"\n  Game Over! Endpunktzahl: {score}")
            print(f"  Korrekte Sequenz war:")
            for i, uid in enumerate(sequence):
                print(f"    {i+1}. {get_card_label(uid)}")

            # Spiel neu starten?
            print("\n  Nochmal spielen? Lege die Game-Toggle-Karte auf...")
            uid = wait_for_card_scan(reader, timeout=15.0)
            if uid == game_toggle_uid:
                score = 0
                level = start_length
                sequence = []
                print("  Neues Spiel!")
            else:
                break

    print("\n  Tschüss! 👋")


# ──────────────────────────────────────────────────────────────────────
# Warte auf Spielstart
# ──────────────────────────────────────────────────────────────────────

def wait_for_game_start(api: OpenHABAPI):
    """
    Wartet bis eine game_toggle Karte aufgelegt wird,
    dann startet das Spiel.
    """
    reader = ACR122U()

    print("\n" + "═"*55)
    print("   🎮 KARTEN-SPIEL WARTEMODUS")
    print("   (Lege die 'Spiel starten' Karte auf)")
    print("   (Ctrl+C zum Beenden)")
    print("═"*55)

    try:
        while True:
            uid = reader.wait_for_card(timeout=5.0)
            if uid:
                reader.wait_for_removal(timeout=5.0)
                reader.disconnect()

                if uid in CARD_ACTIONS:
                    action = CARD_ACTIONS[uid].get("action", "")
                    if action == "game_toggle":
                        print(f"\n  ▶ Spiel gestartet!")
                        run_game(api)
                        print("\n  Warte auf neue Runde...")
                    else:
                        print(f"  (Falsche Karte: {CARD_ACTIONS[uid].get('label', uid)})")
                else:
                    print(f"  (Unbekannte Karte: {uid})")

    except KeyboardInterrupt:
        print("\n\nBeendet.")


# ──────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID Karten-Sequenz-Spiel")
    parser.add_argument("--no-write", "--dry-run", action="store_true",
                        help="Keine openHAB-Befehle senden")
    parser.add_argument("--rounds", type=int, default=1,
                        help="Startlänge der Sequenz (Standard: 1)")
    parser.add_argument("--start", action="store_true",
                        help="Spiel sofort starten (kein Warten auf Karte)")
    args = parser.parse_args()

    if args.no_write:
        DRY_RUN = True

    api = OpenHABAPI(
        base_url=OPENHAB_URL,
        token=OPENHAB_TOKEN,
        username=OPENHAB_USERNAME,
        password=OPENHAB_PASSWORD,
    )

    if not DRY_RUN and not api.test_connection():
        print(f"⚠ openHAB nicht erreichbar: {OPENHAB_URL}")
        print("  Starte trotzdem (nur Konsolenausgabe)...")
        DRY_RUN = True

    if args.start:
        run_game(api, start_length=args.rounds)
    else:
        wait_for_game_start(api)
