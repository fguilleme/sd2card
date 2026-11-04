# Sauvegarde SD — Raspberry Pi

[English documentation](README.en.md)

Application tactile pour copier une carte SD vers un disque USB, vérifier les fichiers, puis proposer le formatage de la carte en FAT32 et l'éjection des deux supports. L'interface fonctionne directement sur le framebuffer Linux, **sans X ni Wayland**.

## Matériel et système utilisés

- Raspberry Pi 4 sous Raspberry Pi OS / Debian 13, en session console.
- Écran SPI ILI9486 de 480 × 320 pixels : `/dev/fb1`, RGB565.
- Tactile résistif ADS7846, détecté dans `/dev/input/event*`.
- Carte SD à sauvegarder dans un lecteur USB externe et disque USB de destination distinct.

La carte microSD qui fait démarrer le Pi est exclue de la sélection. Les pilotes de l'écran et du tactile doivent déjà être configurés ; ce dépôt ne les installe pas.

## Installation et lancement

Les commandes ci-dessous correspondent au compte `pi` et au dossier `/home/pi/sd2card` utilisés sur cette machine.

Dépendances :

```sh
sudo apt install python3 python3-pil python3-dbus udisks2 dosfstools udev util-linux
```

Le compte d'exécution doit pouvoir accéder au framebuffer et au tactile. Ici, `pi` appartient déjà aux groupes `video` et `input`. Sur une autre installation :

```sh
sudo usermod -aG video,input pi
```

Reconnecter la session après un changement de groupes. Lancer ensuite avec `pi`, **sans sudo** :

```sh
cd /home/pi/sd2card
./run-framebuffer.sh
```

La première ouverture propose un calibrage en cinq appuis. L'option `--calibrate` permet de le refaire immédiatement :

```sh
./run-framebuffer.sh --calibrate
```

L'application vérifie la géométrie, les couleurs et les limites mémoire du framebuffer. Le mode RGB565 du pilote `fb_ili9486`, marqué `nonstd=1` par le noyau, est pris en charge. Les tailles de lignes et décalages fournis par le pilote sont respectés.

## Démarrage automatique sans X

Pour désactiver le lancement de X sur ce Pi :

```sh
sudo python3 /home/pi/sd2card/disable-x.py
```

Le script sauvegarde les réglages, commente l'appel direct à `startx` dans le profil de `pi`, configure `multi-user.target` et désactive LightDM. Il conserve la connexion automatique console existante. Il ne ferme pas la session en cours et ne redémarre pas la machine. La commande de restauration est affichée à la fin.

Pour lancer l'application à la connexion automatique sur tty1 :

```sh
python3 /home/pi/sd2card/install-framebuffer-autostart.py
```

Ce second script s'exécute **sans sudo**. Il sauvegarde `~/.bash_profile` et ajoute un bloc limité à `/dev/tty1`. Les connexions SSH gardent leur shell habituel. Il suppose que la connexion automatique console de `pi` est déjà configurée.

Le changement prend effet à la prochaine connexion console ou au prochain démarrage. Pour supprimer ce lancement automatique :

```sh
python3 /home/pi/sd2card/install-framebuffer-autostart.py --disable
```

Sur ce Pi, la console principale utilise fb0 et le petit écran dédié utilise fb1. L'affichage direct peut donc aussi être lancé depuis SSH. Les opérations UDisks ont toutefois besoin des autorisations appropriées : **le lancement automatique dans la session console locale active est le mode d'utilisation normal**. L'application utilise un verrou commun pour empêcher deux instances simultanées.

## Utilisation

1. Brancher la carte SD dans son lecteur externe et le disque USB.
2. Vérifier les informations des deux gros indicateurs. Si un lecteur générique est inconnu, toucher **CARTE SD** ou **DISQUE USB** pour sélectionner le bon support.
3. Toucher **Lancer la copie**. La carte est montée en lecture seule. La progression indique la copie, puis la vérification SHA-256.
4. Après **Copie terminée et vérifiée**, choisir **Effacer la SD et éjecter** si la carte doit être réutilisée. Une confirmation rappelle la carte concernée.
5. Attendre **Terminé — retrait autorisé** avant de retirer les supports.

**Éjecter sans effacer** conserve la carte. **Annuler** interrompt la copie ou les vérifications ; l'annulation peut attendre la fin d'une opération système en cours. Dès que le formatage commence, son annulation est désactivée et il faut le laisser se terminer.

## Réglages et langues

- **Calibrer le tactile** : refaire les cinq appuis. **Annuler** conserve le calibrage précédent.
- **Langue** : choisir **Français** ou **English**. Le changement est immédiat et automatiquement sauvegardé pour les lancements suivants, y compris après redémarrage.
- **Retour** : revenir à l'écran précédent.

Les préférences se trouvent dans :

```text
~/.config/sdbackup/settings.json    # langue
~/.config/sdbackup/touch.json       # calibrage
```

Les noms de fichiers et de supports restent inchangés dans l'interface. Échap ou `q`, depuis le terminal de l'application, demande sa fermeture ; pendant un travail, la sortie attend l'annulation ou la fin du formatage. Le petit écran affiche alors un message d'arrêt explicite plutôt qu'une page de boutons inactifs.

## Organisation des sauvegardes

```text
SD_2026-11-04_18-30-00_123456/
    contenu/             # fichiers, fichiers cachés et dossiers vides
    verification.json    # inventaire et empreintes SHA-256
```

Chaque lancement crée un nouveau dossier daté. Une copie interrompue ou en échec conserve le suffixe `.incomplet`. Les copies précédentes ne sont pas écrasées et les tentatives ne reprennent pas automatiquement les dossiers incomplets.

Il s'agit d'une copie des fichiers, pas d'une image secteur : les secteurs d'amorçage et les permissions Unix ne sont pas sauvegardés. Les dates de modification des fichiers sont conservées dans les limites du système de fichiers de destination.

## Contrôles avant effacement

- Exclusion des disques système, du swap, des volumes chiffrés/LVM, des supports à plusieurs partitions et des montages hors `/media` ou `/run/media`.
- Chaque support doit posséder une seule partition avec un système de fichiers reconnu, un UUID et un accès en écriture. Les supports vierges ne sont pas partitionnés automatiquement.
- Contrôle de l'identité du support, de la génération du périphérique, des points de montage et de la protection en lecture seule de la source.
- Contrôle de l'espace disponible ; refus des fichiers de plus de 4 Gio lorsque le disque de destination est en FAT.
- Refus des liens symboliques et fichiers spéciaux.
- Synchronisation des fichiers et du système de fichiers, puis relecture source/destination et comparaison SHA-256 de chaque fichier.
- Nouvelle vérification complète avant effacement, synchronisation du disque et démontage réussi des deux supports. Un échec à ces étapes bloque le formatage.
- Formatage FAT32 de la partition de la carte, puis extinction logique des lecteurs via UDisks. Les lecteurs partageant leur alimentation avec un autre support sont refusés.

Le formatage rapide prépare la carte à être réutilisée ; ce n'est pas un effacement confidentiel. Une coupure de courant pendant le formatage peut laisser la carte à reformater. La vérification logicielle et les synchronisations ne garantissent pas le comportement du cache interne d'un périphérique matériel.

## Reconnaissance des lecteurs et diagnostic USB

`sd-readers.json` contient les lecteurs identifiés sur cette installation. Un lecteur connu est reconnu par fabricant, produit et numéro de série, indépendamment du port lorsqu'il est unique. Si plusieurs lecteurs annoncent les mêmes identifiants, le port mémorisé sert à les distinguer. Le rôle d'un support n'est jamais déduit de sa seule capacité.

Le lecteur générique `349c:0418` a présenté des erreurs intermittentes `can't set config #1, error -75` au branchement. Dans cet état, Linux ne crée pas le disque : l'application ne peut pas lire la carte. Le lecteur Genesys `05e3:0747` a été détecté correctement et enregistré comme lecteur SD. Sa fiabilité à long terme reste à vérifier.

Pour comparer les périphériques visibles et les messages du noyau :

```sh
lsusb -t
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,TRAN
journalctl -k -b --no-pager -n 60
```

Journal de l'application : `~/.local/state/sdbackup/app.log`, avec rotation. `XDG_CONFIG_HOME` et `XDG_STATE_HOME` permettent de changer les dossiers de configuration et de journal.

## Image de démarrage

Les fichiers du thème Plymouth se trouvent dans `assets/boot/`. Pour installer le visuel depuis une console :

```sh
sudo python3 /home/pi/sd2card/install-appearance-root.py
```

Le script sauvegarde les fichiers concernés sous `/var/backups/sdbackup-appearance/<date>/`, installe le thème et reconstruit les initramfs. Un échec déclenche une restauration des fichiers sauvegardés. Aucun redémarrage immédiat n'est imposé. L'écran SPI peut rester noir avant son initialisation par le noyau.

La source vectorielle et le PNG sont fournis. Leur régénération utilise `python3-cairo` :

```sh
python3 assets/make_background.py
```

## Tests et état de validation

```sh
python3 -m unittest discover -s tests -v
```

Les tests de copie et de formatage utilisent des dossiers temporaires et des périphériques simulés. Ils ne formatent aucun support réel.

Ont été vérifiés sur le matériel : framebuffer RGB565, lecture exclusive du tactile ADS7846, calibrage, navigation des réglages, langues et conservation du choix après relance, démarrage automatique console, autorisations Polkit et affichage d'une carte réelle.

**Un cycle complet copie / vérification / formatage / éjection sur des supports de test reste à valider.**

Cette version contient l'application framebuffer et ses outils de mise en place. Les anciens essais d'interface sous X et les expérimentations USB ne font pas partie de cette version.
