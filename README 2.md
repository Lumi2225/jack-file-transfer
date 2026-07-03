# jack-file-transfer

Transfère des fichiers entre deux ordinateurs en passant uniquement par un **câble jack** (sortie audio → entrée micro) ou même à travers l'air via **enceinte + micro**. Aucune lib de modem, aucun matériel spécial : juste du DSP fait maison (FSK) en Python.

Compatible **Linux / Windows / macOS**.

## Comment ça marche

Un câble jack transporte un signal audio analogique, pas des données numériques directement. Ce projet encode donc le fichier en une suite de tonalités :

- **Bit `0`** → une tonalité à 1200 Hz
- **Bit `1`** → une tonalité à 2200 Hz
- Un **préambule** de calibration (1800 Hz, 1.5s) permet au récepteur de détecter le début de la transmission
- Un **header** contient le nom du fichier, sa taille, et un hash **SHA-256** pour vérifier l'intégrité à l'arrivée

Le décodage utilise l'**algorithme de Goertzel** (une FFT ciblée sur une seule fréquence), qui permet de savoir, pour chaque tranche de temps, si le signal reçu correspond plutôt à la fréquence du bit `0` ou du bit `1`.

C'est volontairement lent (~50 bits/s par défaut, soit ~6 octets/s) pour rester fiable même avec du bruit ambiant ou un câble de qualité moyenne. Pratique pour transférer un petit fichier texte, une clé, une config — pas fait pour des gros fichiers.

## Installation

```bash
pip install -r requirements.txt
```

Sous Linux, si `sounddevice` te dit que PortAudio manque :

```bash
sudo apt install portaudio19-dev   # Debian/Ubuntu
sudo dnf install portaudio-devel   # Fedora
```

Sous Windows et macOS, PortAudio est inclus automatiquement avec le paquet pip, rien à faire de plus.

## Utilisation

### 1. Relier les deux machines

Deux options :

- **Câble jack** : sortie casque de la machine A → entrée micro de la machine B (câble jack mâle-mâle 3.5mm). C'est l'option la plus fiable.
- **Sans câble** : enceinte de la machine A face au micro de la machine B. Ça marche, mais plus sensible au bruit ambiant — monte le volume et réduis la distance si besoin.

### 2. Repérer les périphériques audio (optionnel)

```bash
python send.py --list-devices
python receive.py --list-devices
```

### 3. Lancer la réception (côté machine B, en premier)

```bash
python receive.py --output ./recu/
```

Le script attend en silence la détection du préambule.

### 4. Lancer l'envoi (côté machine A)

```bash
python send.py mon_fichier.txt
```

Le script joue le signal audio, puis termine automatiquement. Le récepteur reconstruit le fichier et vérifie le checksum.

## Options

### `send.py`

| Option | Description |
|---|---|
| `--fast` | Débit doublé (~125 bits/s), moins robuste au bruit |
| `--device N` | Force le périphérique de sortie audio n°N |
| `--list-devices` | Liste les périphériques disponibles |

### `receive.py`

| Option | Description |
|---|---|
| `--fast` | Doit correspondre à l'option utilisée côté émetteur |
| `--device N` | Force le périphérique d'entrée audio n°N |
| `--output DOSSIER` | Dossier où écrire le fichier reçu (défaut : dossier courant) |
| `--timeout N` | Abandonne après N secondes sans détecter de signal |
| `--list-devices` | Liste les périphériques disponibles |

## Limites connues

- **Débit faible** : pensé pour des petits fichiers (quelques Ko). Un fichier de 1 Mo prendrait plusieurs heures au débit par défaut.
- **Pas de correction d'erreur** : si le signal est trop bruité, le checksum SHA-256 détectera la corruption mais le script ne corrige rien automatiquement — il faut relancer le transfert.
- **Sensible au volume** : un volume système trop élevé sature le signal et augmente les erreurs. Si le checksum échoue systématiquement, baisse le volume de sortie.
- **Un seul fichier à la fois** par exécution.

## Idées d'amélioration (contributions bienvenues)

- Correction d'erreur (répétition de bits ou Reed-Solomon) pour plus de robustesse
- Détection automatique du bruit ambiant et ajustement dynamique du débit
- Barre de progression pendant la réception
- Support de plusieurs fichiers / dossiers compressés à la volée

## Licence

MIT — voir [LICENSE](LICENSE).
