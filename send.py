#!/usr/bin/env python3
"""
send.py — Envoie un fichier sous forme de signal audio (via jack, enceinte, etc.)
Compatible Linux / Windows / macOS.

Principe : FSK (Frequency-Shift Keying) fait maison.
- Un chirp de calibration ouvre la transmission (le récepteur s'en sert pour
  se synchroniser et mesurer le niveau de bruit ambiant).
- Chaque bit est encodé comme une tonalité pure : FREQ_0 pour un bit à 0,
  FREQ_1 pour un bit à 1, jouée pendant BIT_DURATION secondes.
- On envoie d'abord un header (nom de fichier + taille + checksum), puis
  les données du fichier, bit par bit.

Usage:
    python send.py fichier.txt
    python send.py fichier.txt --fast      (débit plus rapide, moins robuste)
    python send.py fichier.txt --device 3  (choisir la sortie audio)
"""

import sys
import argparse
import hashlib
import struct
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("Il manque la lib sounddevice. Installe-la avec :")
    print("    pip install sounddevice numpy")
    sys.exit(1)

# --- Paramètres du protocole -------------------------------------------------

SAMPLE_RATE = 44100          # Hz, standard audio
FREQ_0 = 1200                # Hz — représente un bit 0
FREQ_1 = 2200                # Hz — représente un bit 1
FREQ_SYNC_START = 1800       # Hz — tonalité de calibration (chirp simple)

BIT_DURATION_NORMAL = 0.02   # secondes par bit (~50 bits/s, robuste)
BIT_DURATION_FAST = 0.008    # secondes par bit (~125 bits/s, moins robuste)

PREAMBLE_DURATION = 1.5      # secondes de tonalité de calibration
SILENCE_GAP = 0.3            # secondes de silence avant les données

AMPLITUDE = 0.6              # volume (0.0 à 1.0), évite la saturation

MAGIC = b"JACKFT01"          # signature du protocole, en tête du header


def tone(freq, duration, sample_rate=SAMPLE_RATE, amplitude=AMPLITUDE):
    """Génère une tonalité pure avec fondu d'entrée/sortie (évite les clics)."""
    n = int(sample_rate * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    wave = amplitude * np.sin(2 * np.pi * freq * t)

    # Fondu (fade) de quelques millisecondes pour éviter les clics audibles
    # qui abîment la détection de fréquence chez le récepteur.
    fade_len = max(1, int(sample_rate * 0.002))
    if n > 2 * fade_len:
        fade_in = np.linspace(0, 1, fade_len)
        fade_out = np.linspace(1, 0, fade_len)
        wave[:fade_len] *= fade_in
        wave[-fade_len:] *= fade_out

    return wave.astype(np.float32)


def bytes_to_bits(data: bytes):
    """Convertit une suite d'octets en liste de bits (MSB en premier)."""
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def build_header(filename: str, filesize: int, checksum: bytes) -> bytes:
    """
    Header binaire fixe :
      MAGIC (8o) + longueur nom (1o) + nom (variable, utf-8)
      + taille fichier (8o, big-endian) + checksum sha256 (32o)
    """
    name_bytes = filename.encode("utf-8")
    if len(name_bytes) > 255:
        raise ValueError("Nom de fichier trop long (max 255 octets encodé utf-8)")
    header = MAGIC
    header += struct.pack("B", len(name_bytes))
    header += name_bytes
    header += struct.pack(">Q", filesize)
    header += checksum
    return header


def main():
    parser = argparse.ArgumentParser(description="Envoie un fichier via signal audio.")
    parser.add_argument("file", help="Chemin du fichier à envoyer")
    parser.add_argument("--fast", action="store_true", help="Débit rapide (moins robuste)")
    parser.add_argument("--device", type=int, default=None, help="Index du périphérique de sortie audio")
    parser.add_argument("--list-devices", action="store_true", help="Liste les périphériques audio et quitte")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    bit_duration = BIT_DURATION_FAST if args.fast else BIT_DURATION_NORMAL

    with open(args.file, "rb") as f:
        data = f.read()

    checksum = hashlib.sha256(data).digest()
    filename = args.file.split("/")[-1].split("\\")[-1]  # marche sous Linux et Windows
    header = build_header(filename, len(data), checksum)

    payload = header + data
    bits = bytes_to_bits(payload)

    print(f"Fichier : {filename} ({len(data)} octets)")
    print(f"Débit   : {1/bit_duration:.1f} bits/s")
    print(f"Durée estimée : {len(bits) * bit_duration + PREAMBLE_DURATION + SILENCE_GAP:.1f} secondes")

    # --- Construction du signal complet ---
    segments = []

    # 1. Préambule de calibration (tonalité fixe pour que le récepteur se cale)
    segments.append(tone(FREQ_SYNC_START, PREAMBLE_DURATION))

    # 2. Silence court pour marquer la fin du préambule
    segments.append(np.zeros(int(SAMPLE_RATE * SILENCE_GAP), dtype=np.float32))

    # 3. Les données, bit par bit
    for bit in bits:
        freq = FREQ_1 if bit else FREQ_0
        segments.append(tone(freq, bit_duration))

    # 4. Petit silence final
    segments.append(np.zeros(int(SAMPLE_RATE * 0.3), dtype=np.float32))

    signal = np.concatenate(segments)

    print("Lecture en cours... assure-toi que le récepteur est prêt et à l'écoute.")
    sd.play(signal, samplerate=SAMPLE_RATE, device=args.device, blocking=True)
    print("Transmission terminée.")


if __name__ == "__main__":
    main()
