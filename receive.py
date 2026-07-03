#!/usr/bin/env python3
"""
receive.py — Reçoit un fichier envoyé par send.py sous forme de signal audio.
Compatible Linux / Windows / macOS.

Fonctionnement :
1. Écoute en continu le micro/entrée jack.
2. Détecte le préambule (tonalité de calibration) pour savoir quand
   la transmission commence.
3. Découpe le signal en tranches de BIT_DURATION et détermine, pour
   chaque tranche, si elle contient plutôt FREQ_0 ou FREQ_1 (via Goertzel,
   une version allégée de la FFT ciblée sur deux fréquences précises).
4. Reconstruit les octets, lit le header, vérifie le checksum, écrit le fichier.

Usage:
    python receive.py
    python receive.py --fast
    python receive.py --device 2
    python receive.py --output dossier_sortie/
"""

import sys
import argparse
import hashlib
import struct
import time
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("Il manque la lib sounddevice. Installe-la avec :")
    print("    pip install sounddevice numpy")
    sys.exit(1)

# --- Paramètres du protocole (doivent matcher send.py) ----------------------

SAMPLE_RATE = 44100
FREQ_0 = 1200
FREQ_1 = 2200
FREQ_SYNC_START = 1800

BIT_DURATION_NORMAL = 0.02
BIT_DURATION_FAST = 0.008

MAGIC = b"JACKFT01"

# Seuil de détection du préambule (énergie relative)
SYNC_THRESHOLD = 0.15
SYNC_MIN_DURATION = 0.6  # secondes minimum de tonalité stable pour valider le sync


def goertzel_energy(samples: np.ndarray, freq: float, sample_rate: int) -> float:
    """
    Algorithme de Goertzel : calcule l'énergie du signal à une fréquence donnée.
    C'est l'équivalent d'une FFT mais ciblé sur une seule fréquence -> beaucoup
    plus rapide et suffisant ici puisqu'on ne cherche que 2 ou 3 fréquences précises.
    """
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + (n * freq) / sample_rate)
    omega = (2 * np.pi * k) / n
    coeff = 2 * np.cos(omega)

    s_prev = 0.0
    s_prev2 = 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s

    power = s_prev2 ** 2 + s_prev ** 2 - coeff * s_prev * s_prev2
    return power


def bits_to_bytes(bits):
    """Reconstruit des octets à partir d'une liste de bits (MSB en premier)."""
    data = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        data.append(byte)
    return bytes(data)


def parse_header(data: bytes):
    """
    Lit le header en tête des données reçues.
    Retourne (filename, filesize, checksum, offset_apres_header) ou None si invalide.
    """
    if len(data) < len(MAGIC) + 1:
        return None
    if data[:len(MAGIC)] != MAGIC:
        return None
    pos = len(MAGIC)
    name_len = data[pos]
    pos += 1
    if len(data) < pos + name_len + 8 + 32:
        return None
    filename = data[pos:pos + name_len].decode("utf-8", errors="replace")
    pos += name_len
    filesize = struct.unpack(">Q", data[pos:pos + 8])[0]
    pos += 8
    checksum = data[pos:pos + 32]
    pos += 32
    return filename, filesize, checksum, pos


def listen_and_decode(bit_duration, device, timeout, output_dir):
    print("En écoute... (Ctrl+C pour annuler)")
    print("En attente du signal de calibration...")

    chunk_duration = 0.05  # on analyse le flux par tranches de 50ms pour le sync
    chunk_samples = int(SAMPLE_RATE * chunk_duration)

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        device=device,
        dtype="float32",
        blocksize=chunk_samples,
    )

    sync_energy_history = []
    start_time = time.time()

    with stream:
        # --- Phase 1 : détection du préambule ---
        synced = False
        while not synced:
            if timeout and (time.time() - start_time) > timeout:
                print("Timeout : aucun signal détecté.")
                return

            chunk, _ = stream.read(chunk_samples)
            chunk = chunk[:, 0]

            energy_sync = goertzel_energy(chunk, FREQ_SYNC_START, SAMPLE_RATE)
            energy_total = np.sum(chunk ** 2) + 1e-9
            ratio = energy_sync / energy_total

            sync_energy_history.append(ratio > SYNC_THRESHOLD)
            # Garde une fenêtre glissante des dernières détections
            if len(sync_energy_history) > int(SYNC_MIN_DURATION / chunk_duration):
                sync_energy_history.pop(0)

            if len(sync_energy_history) >= int(SYNC_MIN_DURATION / chunk_duration) and all(sync_energy_history):
                synced = True
                print("Préambule détecté ! Attente de la fin de la calibration...")

        # Attendre que le préambule se termine (silence)
        silence_count = 0
        while silence_count < 4:  # ~0.2s de silence attendu
            chunk, _ = stream.read(chunk_samples)
            chunk = chunk[:, 0]
            energy_total = np.sum(chunk ** 2) / len(chunk)
            if energy_total < 0.001:
                silence_count += 1
            else:
                silence_count = 0

        print("Réception des données en cours...")

        # --- Phase 2 : lecture des bits ---
        bit_samples = int(SAMPLE_RATE * bit_duration)
        bits = []
        header_info = None
        expected_total_bits = None
        max_header_bits = (len(MAGIC) + 1 + 255 + 8 + 32) * 8  # pire cas

        while True:
            chunk, _ = stream.read(bit_samples)
            chunk = chunk[:, 0]

            e0 = goertzel_energy(chunk, FREQ_0, SAMPLE_RATE)
            e1 = goertzel_energy(chunk, FREQ_1, SAMPLE_RATE)

            bit = 1 if e1 > e0 else 0
            bits.append(bit)

            # Dès qu'on a assez de bits, on tente de parser le header
            if header_info is None and len(bits) >= 8 and len(bits) % 8 == 0:
                partial_bytes = bits_to_bytes(bits)
                parsed = parse_header(partial_bytes)
                if parsed:
                    filename, filesize, checksum, header_offset = parsed
                    header_info = (filename, filesize, checksum, header_offset)
                    expected_total_bits = (header_offset + filesize) * 8
                    print(f"Header reçu : {filename} ({filesize} octets attendus)")

            if expected_total_bits and len(bits) >= expected_total_bits:
                break

            if len(bits) > max_header_bits and header_info is None:
                print("Erreur : impossible de lire un header valide. Signal trop bruité ?")
                return

    # --- Phase 3 : reconstruction et vérification ---
    all_bytes = bits_to_bytes(bits)
    filename, filesize, expected_checksum, header_offset = header_info
    file_data = all_bytes[header_offset:header_offset + filesize]

    actual_checksum = hashlib.sha256(file_data).digest()

    if actual_checksum != expected_checksum:
        print("⚠️  Attention : le checksum ne correspond pas. Le fichier est probablement corrompu.")
        print("    (Bruit ambiant trop fort, ou débit trop rapide pour la qualité du signal.)")
    else:
        print("✓ Checksum vérifié, fichier intègre.")

    import os
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, filename)
    with open(out_path, "wb") as f:
        f.write(file_data)

    print(f"Fichier sauvegardé : {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Reçoit un fichier via signal audio.")
    parser.add_argument("--fast", action="store_true", help="Débit rapide (doit matcher l'émetteur)")
    parser.add_argument("--device", type=int, default=None, help="Index du périphérique d'entrée audio")
    parser.add_argument("--list-devices", action="store_true", help="Liste les périphériques audio et quitte")
    parser.add_argument("--timeout", type=float, default=0, help="Timeout en secondes avant d'abandonner (0 = infini)")
    parser.add_argument("--output", type=str, default=".", help="Dossier de sortie pour le fichier reçu")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    bit_duration = BIT_DURATION_FAST if args.fast else BIT_DURATION_NORMAL

    try:
        listen_and_decode(bit_duration, args.device, args.timeout or None, args.output)
    except KeyboardInterrupt:
        print("\nAnnulé.")


if __name__ == "__main__":
    main()
