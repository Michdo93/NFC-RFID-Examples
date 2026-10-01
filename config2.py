"""
config.py – Zentrale Konfiguration für RFID/NFC → openHAB Steuerung
====================================================================
HIER alle Einstellungen anpassen, bevor die anderen Skripte gestartet werden.
"""

# ============================================================
# 1. openHAB Verbindung
# ============================================================
OPENHAB_URL   = "http://192.168.1.100:8080"  # Deine openHAB-URL

# --- Authentifizierung (eine der drei Optionen wählen) ---

# Option 1 (empfohlen): API-Token
# Token erstellen: openHAB UI → Einstellungen → API-Tokens → Neues Token
OPENHAB_TOKEN    = ""          # z.B. "oh.MeinToken.U0doM1Lz4kJ..."

# Option 2: Basic-Auth (muss in openHAB manuell aktiviert werden)
OPENHAB_USERNAME = ""          # z.B. "openhab"
OPENHAB_PASSWORD = ""          # z.B. "habopen"

# Option 3: Keine Auth (nur bei lokaler Installation ohne Sicherheit)
# → Token und Username/Password leer lassen

# ============================================================
# 2. Managed Items – nur DIESE Items werden gesteuert/gespeichert
# ============================================================
# Format: "interner_name": "openHAB_Item_Name"

SWITCH_ITEMS = {
    "wohnzimmer_licht":   "Light_Wohnzimmer",
    "schlafzimmer_licht": "Light_Schlafzimmer",
    "kueche_licht":       "Light_Kueche",
}

DIMMER_ITEMS = {
    "wohnzimmer_dimmer":   "Dimmer_Wohnzimmer",
    "schlafzimmer_dimmer": "Dimmer_Schlafzimmer",
}

COLOR_ITEMS = {
    "wohnzimmer_farbe":   "Color_Wohnzimmer",
    "schlafzimmer_farbe": "Color_Schlafzimmer",
}

ROLLERSHUTTER_ITEMS = {
    "wohnzimmer_rollo": "Rollershutter_Wohnzimmer",
}

PLAYER_ITEMS = {
    "wohnzimmer_player": "Player_Wohnzimmer",
}

# Alle Items zusammen (für State-Save/Restore)
ALL_MANAGED_ITEMS = {}
ALL_MANAGED_ITEMS.update(SWITCH_ITEMS)
ALL_MANAGED_ITEMS.update(DIMMER_ITEMS)
ALL_MANAGED_ITEMS.update(COLOR_ITEMS)
ALL_MANAGED_ITEMS.update(ROLLERSHUTTER_ITEMS)
ALL_MANAGED_ITEMS.update(PLAYER_ITEMS)

# Items, die beim State-Save/Restore berücksichtigt werden
# (Rollershutter und Player werden NICHT gespeichert, nur Licht-Items)
STATE_SAVE_ITEMS = {**SWITCH_ITEMS, **DIMMER_ITEMS, **COLOR_ITEMS}

# ============================================================
# 3. Szenen – mehrere Items gleichzeitig setzen
# ============================================================
# Format: "szenen_name": { "interner_item_name": wert, ... }
# Wert ist STRING wie openHAB ihn kennt: "ON"/"OFF", "50", "120,100,50" (HSB für Farbe)

SCENES = {
    "scene_film": {
        "wohnzimmer_licht":   "OFF",
        "wohnzimmer_dimmer":  "20",
        "wohnzimmer_farbe":   "30,100,30",   # Warmes Orange (HSB)
    },
    "scene_hell": {
        "wohnzimmer_licht":   "ON",
        "wohnzimmer_dimmer":  "100",
        "wohnzimmer_farbe":   "60,0,100",    # Weiß
    },
    "scene_nacht": {
        "wohnzimmer_licht":   "OFF",
        "schlafzimmer_dimmer": "5",
        "schlafzimmer_farbe":  "0,100,20",   # Tiefes Rot
    },
    "scene_party": {
        "wohnzimmer_farbe":   "270,100,80",  # Lila
        "schlafzimmer_farbe": "120,100,60",  # Grün
    },
}

# ============================================================
# 4. Farben – vordefinierte HSB-Werte für Color Items
# ============================================================
COLORS = {
    "rot":      "0,100,100",
    "orange":   "30,100,100",
    "gelb":     "60,100,100",
    "gruen":    "120,100,80",
    "cyan":     "180,100,80",
    "blau":     "240,100,100",
    "lila":     "270,100,80",
    "pink":     "300,100,100",
    "weiss":    "0,0,100",
    "warmweiss":"35,30,100",
    "aus":      "0,0,0",
}

# Farbreihenfolge für color_cycle
COLOR_CYCLE_ORDER = ["rot", "orange", "gelb", "gruen", "cyan", "blau", "lila", "pink"]

# ============================================================
# 5. Karten-Definitionen (RFID UIDs → Aktionen)
# ============================================================
# Die UID bekommst du mit 02_read_cards.py heraus.
# Format der UID: Hex-String z.B. "04:A1:B2:C3"
#
# Aktionstypen:
#   toggle:<item>               → Switch/Dimmer an-aus
#   dim_up:<item>               → Dimmer heller (bei Dauerhalten)
#   dim_down:<item>             → Dimmer dunkler (bei Dauerhalten)
#   color:<item>:<farbe>        → Farbe setzen (aus COLORS-Dict)
#   color_cycle:<item>          → Farbe weiterschalten (kurz=nächste, lang=Endlos)
#   scene:<szenen_name>         → Szene aktivieren
#   state_save                  → Zustand-Speichern-Modus aktivieren (30s)
#   state_restore:<karten_id>   → Gespeicherten Zustand dieser Karte wiederherstellen
#   game_toggle                 → Spiel starten/stoppen
#   rollershutter_up:<item>     → Rollladen hoch
#   rollershutter_down:<item>   → Rollladen runter
#   rollershutter_toggle:<item> → Toggeln UP/DOWN
#   player:<item>:<befehl>      → PLAY, PAUSE, NEXT, PREVIOUS, REWIND, FASTFORWARD

CARD_ACTIONS = {
    # --- Beispiel-Karten (UIDs nach dem ersten Scan anpassen!) ---

    # Licht
    "04:AA:BB:CC": {
        "label":  "Wohnzimmer Licht Toggle",
        "action": "toggle:wohnzimmer_licht",
    },
    "04:11:22:33": {
        "label":  "Wohnzimmer Dimmer Heller",
        "action": "dim_up:wohnzimmer_dimmer",
    },
    "04:44:55:66": {
        "label":  "Wohnzimmer Dimmer Dunkler",
        "action": "dim_down:wohnzimmer_dimmer",
    },

    # Farben
    "04:77:88:99": {
        "label":  "Farbe Rot",
        "action": "color:wohnzimmer_farbe:rot",
    },
    "04:AA:BB:DD": {
        "label":  "Farbe Blau",
        "action": "color:wohnzimmer_farbe:blau",
    },
    "04:AA:BB:EE": {
        "label":  "Farbe Grün",
        "action": "color:wohnzimmer_farbe:gruen",
    },
    "04:CC:DD:11": {
        "label":  "Farbe Gelb",
        "action": "color:wohnzimmer_farbe:gelb",
    },
    "04:CC:DD:22": {
        "label":  "Farbe Weiß",
        "action": "color:wohnzimmer_farbe:weiss",
    },
    "04:CC:DD:33": {
        "label":  "Farb-Zyklus Wohnzimmer",
        "action": "color_cycle:wohnzimmer_farbe",
    },

    # Szenen
    "04:EE:FF:11": {
        "label":  "Szene: Film",
        "action": "scene:scene_film",
    },
    "04:EE:FF:22": {
        "label":  "Szene: Hell",
        "action": "scene:scene_hell",
    },
    "04:EE:FF:33": {
        "label":  "Szene: Nacht",
        "action": "scene:scene_nacht",
    },

    # State-Karte
    "04:FF:00:11": {
        "label":  "Zustand SPEICHERN (30s Fenster)",
        "action": "state_save",
    },
    "04:FF:00:22": {
        "label":  "Zustand WIEDERHERSTELLEN (Karte 1)",
        "action": "state_restore:memory_slot_1",
    },
    "04:FF:00:33": {
        "label":  "Zustand WIEDERHERSTELLEN (Karte 2)",
        "action": "state_restore:memory_slot_2",
    },

    # Rollladen
    "04:10:20:30": {
        "label":  "Rollladen Toggle",
        "action": "rollershutter_toggle:wohnzimmer_rollo",
    },

    # Player
    "04:10:20:41": {
        "label":  "Play/Pause",
        "action": "player:wohnzimmer_player:PLAY",
    },
    "04:10:20:42": {
        "label":  "Nächster Track",
        "action": "player:wohnzimmer_player:NEXT",
    },
    "04:10:20:43": {
        "label":  "Vorheriger Track",
        "action": "player:wohnzimmer_player:PREVIOUS",
    },

    # Spiel
    "04:20:30:40": {
        "label":  "Spiel Starten/Stoppen",
        "action": "game_toggle",
    },
}

# ============================================================
# 6. Dimmer-Einstellungen
# ============================================================
DIM_STEP = 10          # Prozent pro Schritt
DIM_INTERVAL = 0.8     # Sekunden zwischen Dimm-Schritten bei Dauerhalten
DIM_MIN = 0
DIM_MAX = 100

# ============================================================
# 7. Timing (Sekunden)
# ============================================================
LONG_PRESS_THRESHOLD = 4.0    # Ab wann gilt "lange gehalten"
SHORT_PRESS_MIN = 0.3         # Kürzer = ignoriert (Rauschen)
STATE_SAVE_WINDOW = 30.0      # Sekunden, um die Ziel-Karte aufzulegen
COLOR_CYCLE_INTERVAL = 1.5    # Sekunden zwischen Farbwechseln bei Dauerhalten

# ============================================================
# 8. State-Speicher Datei
# ============================================================
STATE_FILE = "saved_states.json"   # Datei, in der Zustände persistiert werden

# ============================================================
# 9. Spiel-Konfiguration (05_game.py)
# ============================================================
# Liste der Karten-UIDs in der RICHTIGEN Reihenfolge
GAME_SEQUENCE = [
    "04:77:88:99",   # Rot
    "04:AA:BB:DD",   # Blau
    "04:AA:BB:EE",   # Grün
    "04:CC:DD:11",   # Gelb
]
# openHAB Item, das bei Sieg gesetzt wird
GAME_WIN_ITEM = "Switch_GameWin"
# openHAB Item, das bei Niederlage gesetzt wird
GAME_FAIL_ITEM = "Switch_GameFail"
# Visuelles Feedback: Color-Item blinkt
GAME_FEEDBACK_ITEM = "Color_Wohnzimmer"
