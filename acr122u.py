"""
acr122u.py – Low-Level Helfer für ACR122U via pyscard
======================================================
Kapselt alle PC/SC-Kommunikation mit dem Reader.
Unterstützt MIFARE Classic 1K (RFID) und NTAG2xx/NFC-Karten.

Funktionen:
  - Reader verbinden / trennen
  - Karte erkennen, UID lesen
  - MIFARE Classic: Authentifizieren, Block lesen/schreiben
  - NTAG: Seiten lesen/schreiben
  - Karte entfernen erkennen (Polling)
"""

import time
import logging
from typing import Optional

try:
    from smartcard.System import readers
    from smartcard.CardConnection import CardConnection
    from smartcard.util import toHexString, toBytes
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.CardMonitoring import CardMonitor, CardObserver
except ImportError:
    print("FEHLER: pyscard nicht installiert!")
    print("  sudo apt install -y pcscd python3-pyscard")
    print("  oder: pip install pyscard")
    raise

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# ACR122U APDU-Befehle
# ────────────────────────────────────────────────────────────
CMD_GET_UID              = [0xFF, 0xCA, 0x00, 0x00, 0x00]
CMD_LED_BUZZER_OFF       = [0xFF, 0x00, 0x40, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00]

# MIFARE Classic Authentifizierung
MIFARE_AUTH_A            = 0x60
MIFARE_AUTH_B            = 0x61
DEFAULT_KEY              = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]

# APDU-Statuscodes
SW_OK                    = (0x90, 0x00)
SW_ERROR                 = (0x63, 0x00)


def _sw_ok(response: tuple) -> bool:
    """Prüft ob der APDU-Status OK ist."""
    data, sw1, sw2 = response
    return (sw1, sw2) == SW_OK


class ACR122U:
    """
    Verbindung zum ACR122U NFC/RFID-Reader.

    Verwendung:
        reader = ACR122U()
        if reader.connect():
            uid = reader.get_uid()
            reader.disconnect()
    """

    def __init__(self, reader_index: int = 0):
        self.reader_index = reader_index
        self.connection: Optional[CardConnection] = None
        self._reader = None

    def list_readers(self) -> list:
        """Gibt alle verfügbaren PC/SC-Reader zurück."""
        try:
            r = readers()
            return r
        except Exception as e:
            logger.error(f"Fehler beim Lesen der Reader-Liste: {e}")
            return []

    def connect(self) -> bool:
        """Verbindet mit dem Reader und der aktuellen Karte."""
        try:
            available = readers()
            if not available:
                logger.error("Kein PC/SC Reader gefunden! Ist pcscd gestartet?")
                return False
            if self.reader_index >= len(available):
                logger.error(f"Reader-Index {self.reader_index} nicht verfügbar ({len(available)} Reader gefunden)")
                return False

            self._reader = available[self.reader_index]
            self.connection = self._reader.createConnection()
            self.connection.connect()
            logger.debug(f"Verbunden mit: {self._reader}")
            return True
        except Exception as e:
            logger.debug(f"Verbindung fehlgeschlagen (keine Karte?): {e}")
            return False

    def disconnect(self):
        """Trennt die Verbindung zur Karte (nicht zum Reader)."""
        if self.connection:
            try:
                self.connection.disconnect()
            except Exception:
                pass
            self.connection = None

    def get_uid(self) -> Optional[str]:
        """
        Liest die UID der aktuellen Karte.
        Gibt UID als Hex-String zurück (z.B. "04:A1:B2:C3") oder None bei Fehler.
        """
        if not self.connection:
            return None
        try:
            data, sw1, sw2 = self.connection.transmit(CMD_GET_UID)
            if (sw1, sw2) == SW_OK and data:
                uid = ":".join(f"{b:02X}" for b in data)
                return uid
            else:
                logger.warning(f"UID-Lesefehler: SW={sw1:02X},{sw2:02X}")
                return None
        except Exception as e:
            logger.error(f"get_uid Fehler: {e}")
            return None

    def get_card_type(self) -> str:
        """
        Versucht den Karten-Typ zu ermitteln.
        Gibt zurück: 'MIFARE_CLASSIC', 'NTAG', 'UNKNOWN'
        """
        if not self.connection:
            return "UNKNOWN"
        try:
            # ATR auslesen
            atr = self.connection.getATR()
            atr_hex = "".join(f"{b:02X}" for b in atr)
            logger.debug(f"ATR: {atr_hex}")

            # MIFARE Classic hat SAK 0x08 oder 0x18
            # NTAG/Ultralight hat SAK 0x00
            # Heuristik via ATR:
            if "00" in atr_hex[-4:]:
                # Vereinfachte Erkennung – genauer über SAK
                pass

            # Versuche MIFARE-Authentifizierung (Block 1, Key A)
            uid_bytes = self._get_uid_bytes()
            if uid_bytes and self._mifare_auth(1, uid_bytes):
                return "MIFARE_CLASSIC"
            else:
                return "NTAG"
        except Exception:
            return "UNKNOWN"

    def _get_uid_bytes(self) -> Optional[list]:
        """Interne Methode: UID als Byte-Liste."""
        try:
            data, sw1, sw2 = self.connection.transmit(CMD_GET_UID)
            if (sw1, sw2) == SW_OK:
                return list(data)
            return None
        except Exception:
            return None

    # ────────────────────────────────────────────────────────
    # MIFARE Classic 1K Operationen
    # ────────────────────────────────────────────────────────

    def _mifare_auth(self, block: int, uid: list, key: list = None, key_type: int = MIFARE_AUTH_A) -> bool:
        """Authentifiziert einen MIFARE-Sektor."""
        if key is None:
            key = DEFAULT_KEY
        # Lade Key-Struktur in Reader
        load_key_cmd = [0xFF, 0x82, 0x00, 0x00, 0x06] + key
        data, sw1, sw2 = self.connection.transmit(load_key_cmd)
        if (sw1, sw2) != SW_OK:
            logger.warning(f"Key laden fehlgeschlagen: {sw1:02X},{sw2:02X}")
            return False

        # Authentifiziere Block
        auth_cmd = [0xFF, 0x86, 0x00, 0x00, 0x05,
                    0x01, 0x00, block, key_type, 0x00]
        data, sw1, sw2 = self.connection.transmit(auth_cmd)
        if (sw1, sw2) != SW_OK:
            logger.debug(f"Auth fehlgeschlagen Block {block}: {sw1:02X},{sw2:02X}")
            return False
        return True

    def mifare_read_block(self, block: int, uid: list = None, key: list = None) -> Optional[list]:
        """
        Liest einen 16-Byte Block von einer MIFARE Classic Karte.
        block: Block-Nummer (0-63)
        Gibt Liste von 16 Bytes zurück oder None bei Fehler.
        """
        if uid is None:
            uid = self._get_uid_bytes()
        if uid is None:
            return None

        if not self._mifare_auth(block, uid, key):
            return None

        read_cmd = [0xFF, 0xB0, 0x00, block, 0x10]
        data, sw1, sw2 = self.connection.transmit(read_cmd)
        if (sw1, sw2) == SW_OK:
            return list(data)
        logger.warning(f"Block {block} lesen fehlgeschlagen: {sw1:02X},{sw2:02X}")
        return None

    def mifare_write_block(self, block: int, data_16: list, uid: list = None, key: list = None) -> bool:
        """
        Schreibt 16 Bytes in einen MIFARE Classic Block.
        ACHTUNG: Block 0 (Hersteller) und Trailer-Blöcke (3,7,11,...) NICHT überschreiben!
        """
        if len(data_16) != 16:
            raise ValueError("data_16 muss genau 16 Bytes enthalten!")

        if uid is None:
            uid = self._get_uid_bytes()
        if uid is None:
            return False

        if not self._mifare_auth(block, uid, key):
            return False

        write_cmd = [0xFF, 0xD6, 0x00, block, 0x10] + data_16
        data_resp, sw1, sw2 = self.connection.transmit(write_cmd)
        if (sw1, sw2) == SW_OK:
            return True
        logger.warning(f"Block {block} schreiben fehlgeschlagen: {sw1:02X},{sw2:02X}")
        return False

    def mifare_read_sector(self, sector: int, uid: list = None, key: list = None) -> Optional[list]:
        """Liest alle 3 Datenblöcke eines MIFARE-Sektors (nicht den Trailer)."""
        start_block = sector * 4
        result = []
        for block in range(start_block, start_block + 3):
            data = self.mifare_read_block(block, uid, key)
            if data is None:
                return None
            result.extend(data)
        return result  # 48 Bytes

    def mifare_write_sector(self, sector: int, data_48: list, uid: list = None, key: list = None) -> bool:
        """Schreibt bis zu 48 Bytes (3 Blöcke) in einen MIFARE-Sektor."""
        if len(data_48) > 48:
            raise ValueError("Maximal 48 Bytes pro Sektor (3 Blöcke × 16 Bytes)")
        # Auf 48 Bytes auffüllen
        padded = data_48 + [0x00] * (48 - len(data_48))
        start_block = sector * 4
        uid_bytes = uid or self._get_uid_bytes()
        for i in range(3):
            block = start_block + i
            chunk = padded[i*16:(i+1)*16]
            if not self.mifare_write_block(block, chunk, uid_bytes, key):
                return False
        return True

    # ────────────────────────────────────────────────────────
    # NTAG2xx / NFC Ultralight Operationen
    # ────────────────────────────────────────────────────────

    def ntag_read_page(self, page: int) -> Optional[list]:
        """Liest eine 4-Byte-Seite von einer NTAG-Karte."""
        cmd = [0xFF, 0xB0, 0x00, page, 0x04]
        data, sw1, sw2 = self.connection.transmit(cmd)
        if (sw1, sw2) == SW_OK:
            return list(data)
        logger.warning(f"NTAG Seite {page} lesen fehlgeschlagen: {sw1:02X},{sw2:02X}")
        return None

    def ntag_write_page(self, page: int, data_4: list) -> bool:
        """Schreibt 4 Bytes in eine NTAG-Seite."""
        if len(data_4) != 4:
            raise ValueError("ntag_write_page erwartet genau 4 Bytes!")
        cmd = [0xFF, 0xD6, 0x00, page, 0x04] + data_4
        data_resp, sw1, sw2 = self.connection.transmit(cmd)
        if (sw1, sw2) == SW_OK:
            return True
        logger.warning(f"NTAG Seite {page} schreiben fehlgeschlagen: {sw1:02X},{sw2:02X}")
        return False

    def ntag_read_all(self, max_pages: int = 45) -> list:
        """Liest alle NTAG-Seiten (Standard: 45 für NTAG215)."""
        all_data = []
        for page in range(4, max_pages):  # Seiten 0-3 sind Header
            data = self.ntag_read_page(page)
            if data is None:
                break
            all_data.extend(data)
        return all_data

    # ────────────────────────────────────────────────────────
    # Polling – Karte warten / überwachen
    # ────────────────────────────────────────────────────────

    def wait_for_card(self, timeout: float = 60.0, poll_interval: float = 0.2) -> Optional[str]:
        """
        Wartet bis eine Karte aufgelegt wird.
        Gibt UID zurück oder None wenn Timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connect():
                uid = self.get_uid()
                if uid:
                    return uid
                self.disconnect()
            time.sleep(poll_interval)
        return None

    def wait_for_removal(self, timeout: float = 60.0, poll_interval: float = 0.1) -> bool:
        """
        Wartet bis die Karte entfernt wird.
        Gibt True zurück wenn entfernt, False bei Timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                uid = self.get_uid()
                if uid is None:
                    return True
            except Exception:
                return True
            time.sleep(poll_interval)
        return False

    def is_card_present(self) -> bool:
        """Prüft ob aktuell eine Karte aufliegt."""
        try:
            uid = self.get_uid()
            return uid is not None
        except Exception:
            return False


# ────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ────────────────────────────────────────────────────────────

def string_to_bytes_padded(text: str, length: int) -> list:
    """Konvertiert String in Byte-Liste, auf `length` Bytes aufgefüllt/gekürzt."""
    encoded = text.encode("utf-8")[:length]
    return list(encoded) + [0x00] * (length - len(encoded))


def bytes_to_string(data: list) -> str:
    """Konvertiert Byte-Liste in String (Null-Bytes werden entfernt)."""
    return bytes(data).replace(b"\x00", b"").decode("utf-8", errors="ignore")


def uid_to_filename(uid: str) -> str:
    """Konvertiert UID in dateisystemfreundlichen Dateinamen."""
    return uid.replace(":", "_")
