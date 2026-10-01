# RFID/NFC → openHAB Steuerung

## Projektstruktur

```
rfid_openhab/
├── README.md                  ← Diese Datei
├── requirements.txt           ← Alle Python-Abhängigkeiten
├── config.py                  ← Zentrale Konfiguration (Items, Karten-IDs)
│
├── 01_write_cards.py          ← Karten beschreiben (RFID/NFC)
├── 02_read_cards.py           ← Karten lesen / Diagnose
├── 03_reset_card.py           ← Karte zurücksetzen / überschreiben
│
├── 04_controller.py           ← Hauptprogramm: Karten erkennen → openHAB steuern
│                                 (Toggle, Dimmen, Farbe, Szenen, State-Save/Restore)
│
├── 05_game.py                 ← Karten-Ratespiel mit openHAB-Integration
│
└── acr122u.py                 ← Low-Level ACR122U Helfer-Klasse (pyscard)
```

## Installation

### System-Abhängigkeiten (Linux/Raspberry Pi)
```bash
sudo apt update
sudo apt install -y pcscd pcsc-tools swig python3-dev
# pn533_usb Kernel-Modul sperren (blockiert sonst pcscd):
echo "blacklist pn533_usb" | sudo tee /etc/modprobe.d/blacklist-nfc.conf
echo "blacklist nfc" | sudo tee -a /etc/modprobe.d/blacklist-nfc.conf
sudo update-initramfs -u
sudo reboot
```

### Python-Pakete
```bash
pip install -r requirements.txt
```

### pcscd Service starten
```bash
sudo systemctl enable pcscd
sudo systemctl start pcscd
```

## Konfiguration

Alle Einstellungen in `config.py` anpassen:
- openHAB URL + Token
- Items (Lampen, Dimmer, Farben, Rollläden, Player)
- Karten-IDs und ihre Aktionen

## Karten-Typen

| Karten-Typ        | Produkt           | Format        | Verwendung                          |
|-------------------|-------------------|---------------|-------------------------------------|
| RFID MIFARE 1K    | B07TVJPTM7        | ISO 14443A    | Alle Steuerkarten (Controller)      |
| NFC NTAG215       | B0B9BPPP17        | NDEF          | Gerätekarten (an Lampen kleben)     |

## Karten-Aktionen (Übersicht)

| Aktion-Typ         | Beschreibung                                      |
|--------------------|---------------------------------------------------|
| `toggle`           | Switch an/aus toggeln                             |
| `dim_up`           | Dimmer um X% erhöhen (bei Dauerhalten)            |
| `dim_down`         | Dimmer um X% verringern (bei Dauerhalten)         |
| `color_<farbe>`    | Farbe setzen (rot, grün, blau, gelb, weiß, ...)   |
| `color_cycle`      | Farbe endlos weiterschalten (bei Dauerhalten)     |
| `scene_<name>`     | Szene (mehrere Items gleichzeitig) aktivieren     |
| `state_save`       | Aktuellen Zustand auf nächste Karte speichern     |
| `state_restore`    | Gespeicherten Zustand wiederherstellen            |
| `game_toggle`      | Spiel starten/stoppen                             |
| `rollershutter_up` | Rollladen hoch                                    |
| `rollershutter_down`| Rollladen runter                                 |
| `player_<cmd>`     | PLAY, PAUSE, NEXT, PREVIOUS, REWIND, FASTFORWARD  |

## Timing-Logik (Controller)

```
Karte aufgelegt:
  < 0.5s  → ignorieren (Rauschen)
  0.5-4s  → Kurz-Aktion (toggle, color_set, scene, state_restore)
  > 4s    → Lang-Aktion (dim_up/down läuft in Schritten, color_cycle läuft)
  
State-Save Modus:
  Karte "state_save" → 30s Fenster
  Innerhalb 30s andere Karte auflegen → Zustand darauf speichern
  Nach 30s → Karte gilt als state_restore
```

## Spiel: "Karten-Memory" (05_game.py)

- Mehrere RFID-Karten haben Farb- oder Helligkeitswerte
- Ziel: Karten in der richtigen Reihenfolge auflegen
- Richtige Reihenfolge → Lampe blinkt grün / openHAB Item wird gesetzt
- Falsche Reihenfolge → Lampe blinkt rot
- Karte "game_toggle" startet / bricht ab
