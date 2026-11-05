"""French/English UI text. Device labels and filenames are kept as user data."""
import re

LANGUAGES = {'fr': 'Français', 'en': 'English'}

ENGLISH = {
    'Paysage (0°)': 'Landscape (0°)',
    'Portrait (90°)': 'Portrait (90°)',
    'Paysage inversé (180°)': 'Landscape inverted (180°)',
    'Portrait inversé (270°)': 'Portrait inverted (270°)',
    'Orientation': 'Orientation',
    'Normale (0°)': 'Normal (0°)',
    'Inversée (180°)': 'Upside down (180°)',
    'Écriture APFS non prise en charge sur ce Raspberry Pi. Utilisez un disque en exFAT ou NTFS.': 'APFS writing is not supported on this Raspberry Pi. Use an exFAT or NTFS drive.',
    'Disque en lecture seule. Pour NTFS, arrêtez complètement Windows et vérifiez le disque sous Windows.': 'Drive is read-only. For NTFS, fully shut down Windows and check the drive in Windows.',
    'Le disque de destination est passé en lecture seule.': 'The destination drive became read-only.',
    'Un fichier dépasse 4 Go : utilisez un disque USB en exFAT, NTFS ou ext4.': 'A file exceeds 4 GB: use an exFAT, NTFS or ext4 USB drive.',
    'SAUVEGARDE SD': 'SD BACKUP',
    'Réglages': 'Settings',
    'Langue': 'Language',
    'Calibrer le tactile': 'Calibrate touchscreen',
    'Retour': 'Back',
    'Annuler': 'Cancel',
    'Continuer': 'Continue',
    'Compris': 'OK',
    'Recommencer': 'Try again',
    'Suite': 'Next',
    'CARTE SD': 'SD CARD',
    'DISQUE USB': 'USB DRIVE',
    'Non détectée': 'Not detected',
    'Non détecté': 'Not detected',
    'Toucher pour choisir': 'Tap to select',
    'En attente des supports': 'Waiting for media',
    'Insérez la carte SD et le disque USB.': 'Insert the SD card and USB drive.',
    'Prêt à sauvegarder': 'Ready to back up',
    'Vérifiez les supports puis lancez la copie.': 'Check the media, then start the backup.',
    'Touchez CARTE SD pour choisir votre carte.': 'Tap SD CARD to select your card.',
    'Insérez le disque USB de sauvegarde.': 'Insert the USB backup drive.',
    'Préparation des supports…': 'Preparing media…',
    'Ne retirez aucun support.': 'Do not remove any media.',
    'Dernière vérification…': 'Final verification…',
    'Éjection en cours…': 'Ejecting media…',
    'Ne retirez pas les supports avant la fin.': 'Wait until ejection is complete.',
    'Annulation en cours…': 'Cancelling…',
    'Copie': 'Copying',
    'Vérification SHA-256': 'SHA-256 verification',
    'Formatage FAT32 en cours…': 'Formatting as FAT32…',
    'Éjection des deux supports…': 'Ejecting both media…',
    'Copie terminée et vérifiée': 'Backup complete and verified',
    'Terminé — retrait autorisé': 'Done — safe to remove',
    'Détection indisponible': 'Media detection unavailable',
    'Opération interrompue': 'Operation interrupted',
    'Opération échouée': 'Operation failed',
    'Choisir la carte SD': 'Select the SD card',
    'Choisir le disque USB': 'Select the USB drive',
    'Aucun support disponible': 'No media available',
    'Lancer la copie': 'Start backup',
    'Effacer la SD et éjecter': 'Erase SD and eject both',
    'Éjecter sans effacer': 'Eject without erasing',
    'Effacer cette carte SD ?': 'Erase this SD card?',
    'Effacer la SD': 'Erase SD card',
    'Calibration enregistrée': 'Calibration saved',
    'Le tactile est prêt. Testez les boutons au doigt.': 'The touchscreen is ready. Try the buttons with your finger.',
    'Calibration à refaire': 'Calibration needs repeating',
    'Enregistrement impossible': 'Could not save settings',
    'Application fermée': 'Application closed',
    'Pour relancer depuis le terminal :': 'To restart from the terminal:',
    'Dans le dossier /home/pi/sd2card': 'In /home/pi/sd2card',
    'Carte effacée en FAT32. Les deux supports peuvent être retirés.': 'SD formatted as FAT32. Both media can be removed.',
    'Supports éjectés. La carte SD est conservée. Retrait autorisé.': 'Media ejected. SD card kept intact. Safe to remove.',
    'Opération annulée. La carte SD est conservée.': 'Operation cancelled. SD card kept intact.',
    'La carte ne peut pas être protégée en lecture seule.': 'The SD card cannot be mounted read-only.',
    'La source et la destination désignent le même support.': 'Source and destination refer to the same device.',
    'Un fichier dépasse 4 Go : utilisez un disque USB en exFAT ou ext4.': 'A file exceeds 4 GB: use an exFAT or ext4 USB drive.',
    'Un support a été démonté pendant le travail.': 'Media was unmounted during the operation.',
    'La protection en lecture seule de la carte a changé.': 'The SD card read-only protection changed.',
    'Interface indisponible : effacement refusé.': 'Interface unavailable: erasing blocked.',
    'Support retiré, remplacé ou configuration modifiée. Recommencez la copie.': 'Media removed, replaced or changed. Start a new backup.',
    'Utilisez deux supports et deux lecteurs distincts.': 'Use two separate media and two separate readers.',
    'Ce lecteur ne permet pas une éjection électrique sûre. Utilisez un autre lecteur USB.': 'This reader cannot be powered off safely. Use another USB reader.',
    'Ce boîtier USB partage son alimentation avec un autre support. Utilisez deux lecteurs indépendants.': 'This USB enclosure shares power with other media. Use two independent readers.',
    'Aucune copie vérifiée disponible.': 'No verified backup available.',
    'Le contenu de la carte a changé.': 'The SD card contents have changed.',
    'Le contenu de la sauvegarde a changé.': 'The backup contents have changed.',
    'La carte a changé pendant la vérification.': 'The SD card changed during verification.',
    'La carte ne contient aucun fichier à sauvegarder.': 'The SD card contains no files to back up.',
    'Espace libre insuffisant sur le disque USB.': 'Not enough free space on the USB drive.',
    'Le disque USB est en lecture seule.': 'The USB drive is read-only.',
    'Échec de synchronisation du disque': 'Drive synchronization failed',
    'Matrice de calibration invalide.': 'Invalid calibration matrix.',
    'Calibration tactile dégénérée.': 'Invalid touchscreen calibration.',
    'Trois points de calibration sont nécessaires.': 'Three calibration points are required.',
    'Appuis trop proches : recommencez la calibration.': 'Touch points are too close: repeat calibration.',
    'Les appuis ne concordent pas : recommencez la calibration.': 'Touch points do not match: repeat calibration.',
}

TEMPLATES = {
    'Calibration {step}/5 : touchez la cible': 'Calibration {step}/5: tap the target',
    '{label} · {size} Go · {partition}': '{label} · {size} GB · {partition}',
    '{label}\n{size} Go · {partition}': '{label}\n{size} GB · {partition}',
    '{label} • {size} Go\n{partition}\nCopie vérifiée sur {target}.\nLe contenu de la SD sera effacé en FAT32.':
        '{label} • {size} GB\n{partition}\nBackup verified on {target}.\nThe SD contents will be erased as FAT32.',
    'Fichier spécial ou lien non pris en charge : {filename}': 'Unsupported special file or link: {filename}',
    'Vérification échouée : {filename}. Carte conservée.': 'Verification failed: {filename}. SD card kept intact.',
}


def _pattern(template):
    result = ''
    for part in re.split(r'(\{\w+\})', template):
        result += '(?P<' + part[1:-1] + '>.+?)' if part.startswith('{') else re.escape(part)
    return re.compile(result, re.DOTALL)


PATTERNS = [(_pattern(source), target) for source, target in TEMPLATES.items()]


def translate(text, language='fr'):
    text = str(text)
    if language != 'en':
        return text
    if text in ENGLISH:
        return ENGLISH[text]
    for pattern, target in PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return target.format(**match.groupdict())
    # Progress messages combine a translated phase and a percentage.
    phase, separator, percent = text.rpartition(' · ')
    if separator and re.fullmatch(r'\d+ %', percent) and phase in ENGLISH:
        return ENGLISH[phase] + separator + percent
    return text
