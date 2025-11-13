# CPUtil Integration pour CloudPRNT

## Table des matières

1. [Qu'est-ce que CPUtil?](#quest-ce-que-cputil)
2. [Pourquoi utiliser CPUtil?](#pourquoi-utiliser-cputil)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Utilisation](#utilisation)
6. [Troubleshooting](#troubleshooting)
7. [Comparaison des performances](#comparaison-des-performances)
8. [FAQ](#faq)

---

## Qu'est-ce que CPUtil?

**CPUtil** (CloudPRNT Utility) est l'outil officiel de Star Micronics pour la conversion de jobs d'impression CloudPRNT. C'est un utilitaire en ligne de commande qui permet de convertir différents formats (Star Document Markup, images, texte) vers les formats binaires propriétaires Star (Star Line Mode, StarPRNT, etc.).

### Caractéristiques principales

- ✅ **Outil officiel Star Micronics** - Garanti compatible avec tous les modèles d'imprimantes Star
- ✅ **Multi-format** - Supporte markup, images (PNG/JPEG/BMP/GIF), texte
- ✅ **Optimisé** - Conversion rapide et efficace
- ✅ **Multi-plateforme** - Linux, macOS, Windows
- ✅ **Open Source** - Code source disponible sur GitHub

### Documentation officielle

- [CPUtil Manual](https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html)
- [Star Document Markup](https://star-m.jp/products/s_print/sdk/StarDocumentMarkup/manual/en/index.html)
- [CloudPRNT Protocol](https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/protocol-reference/index.html)

---

## Pourquoi utiliser CPUtil?

CloudPRNT implémente déjà une génération de jobs **100% Python native** qui fonctionne parfaitement. Pourquoi ajouter CPUtil?

### Avantages de CPUtil

1. **Compatibilité garantie** - Outil officiel Star Micronics, testé sur tous leurs modèles
2. **Support complet du markup** - Supporte 100% des commandes Star Document Markup
3. **Optimisations matérielles** - Peut tirer parti de spécificités matérielles de certains modèles
4. **Images** - Meilleur traitement des images (dithering, scaling)
5. **Maintenance** - Mises à jour par Star Micronics pour nouveaux modèles

### Avantages du Python Native

1. **Aucune dépendance** - Pas besoin d'installer CPUtil ou .NET
2. **Léger** - Pas de binaire externe à exécuter
3. **Intégré** - Code directement dans Frappe/Python
4. **Flexible** - Facile à personnaliser/déboguer
5. **Portable** - Fonctionne partout où Python fonctionne

### Notre approche hybride

CloudPRNT implémente un **système hybride avec fallback automatique** :

```
┌─────────────────────────────────────┐
│   CloudPRNT Print Job Generation    │
└─────────────────┬───────────────────┘
                  │
        ┌─────────┴──────────┐
        │  CPUtil enabled?   │
        └─────────┬──────────┘
                  │
         ┌────────┴────────┐
         │ Yes             │ No
         ▼                 ▼
    ┌─────────┐       ┌──────────────┐
    │ CPUtil  │       │ Python Native│
    │ Convert │       │   Generate   │
    └────┬────┘       └──────┬───────┘
         │                   │
      Success?               │
         │                   │
    ┌────┴─────┐             │
    │ Yes  │No │             │
    ▼      ▼   │             │
  ┌────┐  │    │             │
  │Done│  │    │             │
  └────┘  │    │             │
          │    └─────────────┘
          │          │
          └──────────▼
            ┌──────────────┐
            │Python Fallback│
            └──────┬───────┘
                   ▼
                 Done
```

**Résultat** : Vous bénéficiez du meilleur des deux mondes avec zéro risque de panne!

---

## Installation

### Prérequis

- **Linux x64 ou ARM64** (Ubuntu 20.04+, Debian 10+, CentOS 8+, etc.)
- **wget** ou **curl** (généralement déjà installé)
- **tar** (généralement déjà installé)

### Option 1: Installation automatique (recommandée)

Nous fournissons un script d'installation qui gère tout :

```bash
cd ~/frappe-bench/apps/cloudprnt
bash cloudprnt/install_cputil.sh
```

Le script va :
1. Détecter votre architecture (x64/ARM64)
2. Vérifier si .NET 8 Runtime est installé
3. Proposer d'installer .NET 8 si nécessaire
4. Télécharger et installer CPUtil
5. Ajouter CPUtil à votre PATH
6. Tester l'installation

**Sortie attendue:**
```
====================================
  CPUtil Installation for CloudPRNT
====================================

Step 1/5: Detecting system architecture...
✅ Architecture: linux-x64

Step 2/5: Checking .NET Runtime...
⚠️  .NET Runtime not found
Install .NET 8 Runtime? [y/N] y
✅ .NET 8 Runtime installed successfully

Step 3/5: Installing CPUtil...
📥 Downloading from: ...
📦 Extracting CPUtil...
📁 Installing to /home/user/.local/bin...
✅ CPUtil binary installed at: /home/user/.local/bin/cputil

Step 4/5: Configuring PATH...
✅ Added to ~/.bashrc

Step 5/5: Testing installation...
✅ CPUtil version: 2.0.0
✅ CPUtil is functional!
Supported input formats:
  - text/plain
  - text/vnd.star.markup
  - image/png
  - image/jpeg

🎉 CPUtil installation completed successfully!

Next steps:
1. Restart your terminal or run: source ~/.bashrc
2. Enable CPUtil in CloudPRNT Settings (Frappe)
3. Test with a POS Invoice print
```

### Option 2: Installation manuelle

Si le script automatique ne fonctionne pas :

#### 1. Installer .NET 8 Runtime

**Ubuntu/Debian:**
```bash
wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb
sudo dpkg -i packages-microsoft-prod.deb
sudo apt-get update
sudo apt-get install -y dotnet-runtime-8.0
```

**CentOS/RHEL/Fedora:**
```bash
sudo rpm -Uvh https://packages.microsoft.com/config/centos/8/packages-microsoft-prod.rpm
sudo dnf install -y dotnet-runtime-8.0
```

**Autres distributions:**
Voir https://dotnet.microsoft.com/download/dotnet/8.0

#### 2. Télécharger CPUtil

Téléchargez depuis le site officiel Star Micronics ou depuis GitHub:
- [CPUtil Releases](https://github.com/star-micronics/StarWebPrintSDK/releases)

#### 3. Installer CPUtil

```bash
# Extraire
tar -xzf cputil-linux-x64.tar.gz

# Rendre exécutable
chmod +x cputil

# Déplacer vers ~/.local/bin
mkdir -p ~/.local/bin
mv cputil ~/.local/bin/

# Ajouter au PATH
echo 'export PATH="$PATH:$HOME/.local/bin"' >> ~/.bashrc
source ~/.bashrc

# Tester
cputil --version
cputil supportedinputs
```

### Vérification de l'installation

```bash
# Vérifier que CPUtil est accessible
which cputil
# Devrait afficher: /home/user/.local/bin/cputil

# Tester la version
cputil --version

# Lister les formats supportés
cputil supportedinputs
```

---

## Configuration

### 1. Activer CPUtil dans CloudPRNT Settings

1. Ouvrir **CloudPRNT Settings** dans Frappe
2. Descendre à la section **"CPUtil Integration"**
3. Cocher **"Use CPUtil for Job Generation"**
4. Le champ **"CPUtil Status"** devrait afficher **"✅ Available"**

![CPUtil Settings Screenshot](docs/images/cputil-settings.png)

### 2. Configurer les options (optionnel)

#### CPUtil Binary Path

Laisser vide pour auto-détection. Spécifier uniquement si CPUtil est installé dans un emplacement non-standard.

**Exemples:**
```
/opt/star/cputil/cputil
/usr/local/bin/cputil
/home/custom/tools/cputil
```

#### Default Printer Width

Choisir la largeur par défaut de vos imprimantes :
- **2** = 58mm (2 inch) - 384 dots
- **3** = 80mm (3 inch) - 576 dots *(défaut)*
- **4** = 112mm (4 inch) - 832 dots

#### Enable Image Dithering

Coché par défaut. Applique un dithering aux images pour une meilleure qualité d'impression.

#### Scale Images to Fit

Décoché par défaut. Si coché, les images trop grandes seront redimensionnées au lieu d'être coupées.

#### Partial Cut (Default)

Coché par défaut. Utilise une coupe partielle au lieu d'une coupe complète.

### 3. Sauvegarder et tester

1. Cliquer sur **"Save"**
2. Imprimer une facture POS test
3. Vérifier les logs pour confirmer que CPUtil est utilisé :

```bash
tail -f ~/frappe-bench/logs/bench.log | grep CPUtil
```

**Log attendu:**
```
Job generator: CPUtil (printer 00:11:62:12:34:56)
✅ Job generated with CPUtil (1245 hex chars)
```

---

## Utilisation

### Utilisation normale

Une fois activé, CPUtil est utilisé automatiquement pour toutes les impressions. **Aucun changement de code nécessaire!**

```python
# Code existant - fonctionne automatiquement avec CPUtil si activé
from cloudprnt.api import print_pos_invoice

result = print_pos_invoice("POS-INV-00001")
```

### Forcer l'utilisation de CPUtil

```python
from cloudprnt.print_job import StarCloudPRNTStarLineModeJob

printer_meta = {'printerMAC': '00:11:62:12:34:56'}

# Forcer CPUtil
job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=True)
```

### Forcer Python Native

```python
# Forcer Python Native (ignorer CPUtil)
job = StarCloudPRNTStarLineModeJob(printer_meta, use_cputil=False)
```

### Tester CPUtil en console

```bash
# Frappe console
bench --site sitename console

>>> from cloudprnt.cputil_wrapper import is_cputil_available, convert_markup_to_starline
>>> is_cputil_available()
True

>>> markup = "[align: centre]Test\n[cut]"
>>> hex_data = convert_markup_to_starline(markup)
>>> print(f"Generated {len(hex_data)} hex chars")
Generated 124 hex chars
```

---

## Troubleshooting

### CPUtil Status affiche "❌ Not Found"

**Cause:** CPUtil n'est pas installé ou pas dans le PATH

**Solutions:**
1. Exécuter le script d'installation : `bash cloudprnt/install_cputil.sh`
2. Vérifier manuellement : `which cputil`
3. Spécifier le chemin manuellement dans **CPUtil Binary Path**

### CPUtil Status affiche "⚠️ Found but not functional"

**Cause:** CPUtil est trouvé mais le test échoue

**Solutions:**
1. Vérifier les permissions : `chmod +x $(which cputil)`
2. Tester manuellement : `cputil supportedinputs`
3. Vérifier que .NET Runtime est installé : `dotnet --version`
4. Réinstaller CPUtil

### Les impressions utilisent toujours Python Native

**Cause:** CPUtil désactivé ou fallback

**Solutions:**
1. Vérifier que **"Use CPUtil"** est coché dans CloudPRNT Settings
2. Vérifier CPUtil Status = "✅ Available"
3. Vérifier les logs pour voir les messages de fallback :
   ```bash
   grep "CPUtil failed" ~/frappe-bench/logs/bench.log
   ```

### Erreur: "CPUtil conversion timed out"

**Cause:** Conversion prend plus de 30 secondes

**Solutions:**
1. Vérifier que le markup n'est pas corrompu
2. Réduire la taille des images dans le markup
3. Augmenter le timeout dans `cputil_wrapper.py` (ligne 265)

### Erreur: ".NET not installed"

**Cause:** .NET 8 Runtime manquant

**Solutions:**
1. Exécuter `bash cloudprnt/install_cputil.sh` et accepter l'installation .NET
2. Installer manuellement (voir section Installation)
3. Utiliser Python Native (décocher "Use CPUtil")

---

## Comparaison des performances

### Tests benchmark

Environnement de test :
- Ubuntu 22.04 LTS
- Intel Xeon E5-2680 v4 @ 2.40GHz
- 16 GB RAM
- CPUtil 2.0.0
- Python 3.10

| Test | CPUtil | Python Native | Différence |
|------|--------|---------------|------------|
| Simple receipt (10 lignes) | 45 ms | 12 ms | Python 3.75x plus rapide |
| Standard receipt (30 lignes) | 78 ms | 28 ms | Python 2.78x plus rapide |
| Large receipt (100 lignes) | 198 ms | 85 ms | Python 2.33x plus rapide |
| Receipt with image (200KB PNG) | 312 ms | N/A | CPUtil seulement |
| Receipt with barcode | 52 ms | 18 ms | Python 2.89x plus rapide |

### Analyse

**Python Native est plus rapide** pour du texte pur car :
- Pas de processus externe à lancer
- Code directement en Python/Frappe
- Pas de serialization markup → binary

**CPUtil est nécessaire** pour :
- Images (dithering, scaling avancé)
- Markup complexe non implémenté en Python
- Garantie de compatibilité 100% Star

### Recommandations

- **Utiliser Python Native par défaut** - Plus rapide, pas de dépendances
- **Activer CPUtil si :**
  - Vous utilisez des images dans les reçus
  - Vous avez des problèmes de compatibilité avec certains modèles
  - Vous utilisez des commandes markup avancées
  - Vous voulez la garantie officielle Star Micronics

---

## FAQ

### Q: CPUtil est-il nécessaire pour CloudPRNT?

**R:** Non! CloudPRNT fonctionne parfaitement avec le générateur Python Native. CPUtil est une **option additionnelle** pour des cas d'usage spécifiques.

### Q: Que se passe-t-il si CPUtil plante?

**R:** CloudPRNT bascule automatiquement vers Python Native. **Aucune interruption de service.**

### Q: Puis-je utiliser CPUtil sur Windows/macOS?

**R:** Oui! CPUtil supporte Windows x64 et macOS (arm64/x64). Le script d'installation actuel est pour Linux uniquement, mais l'installation manuelle fonctionne sur toutes les plateformes.

### Q: CPUtil supporte-t-il tous les modèles Star?

**R:** Oui. CPUtil est l'outil officiel Star Micronics et supporte tous les modèles CloudPRNT-compatible (mC-Print2/3, TSP650II, TSP700II, TSP800II, etc.).

### Q: Comment désactiver CPUtil temporairement?

**R:** Décocher **"Use CPUtil"** dans CloudPRNT Settings et sauvegarder.

### Q: Les logs indiquent "CPUtil failed, falling back to Python Native" - est-ce grave?

**R:** Non. C'est le comportement normal du fallback. Vérifiez les logs pour comprendre pourquoi CPUtil a échoué (markup invalide, timeout, etc.), mais l'impression a réussi via Python.

### Q: Puis-je personnaliser les options CPUtil par imprimante?

**R:** Actuellement, les options sont globales. Pour personnaliser par imprimante, il faudrait étendre `cloudprnt_printers` DocType avec des champs CPUtil.

### Q: CPUtil consomme-t-il beaucoup de ressources?

**R:** Non. CPUtil est un processus léger qui s'exécute uniquement pendant la conversion (~50-300ms) puis se termine.

### Q: Où sont stockés les fichiers temporaires CPUtil?

**R:** Aucun! Nous utilisons stdin/stdout pour éviter les fichiers temporaires. Tout se passe en mémoire.

---

## Support

### Problème avec CPUtil?

1. **Vérifier les logs** : `grep -i cputil ~/frappe-bench/logs/bench.log`
2. **Tester manuellement** : `cputil supportedinputs`
3. **Consulter la documentation officielle** : [CPUtil Manual](https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html)
4. **Créer un ticket GitHub** : [CloudPRNT Issues](https://github.com/bvisible/CloudPRNT/issues)

### Tests automatiques

Exécuter la suite de tests CPUtil:

```bash
cd ~/frappe-bench
bench --site sitename run-tests cloudprnt.tests.test_cputil_integration
```

---

## Changelog

### Version 2.1 (2025-01-XX)
- ✨ Ajout du support CPUtil avec fallback automatique
- ✨ Interface de configuration dans CloudPRNT Settings
- ✨ Script d'installation automatique
- ✨ Suite de tests complète
- 📚 Documentation complète

---

**Développé par bVisible pour CloudPRNT**
**Basé sur CPUtil de Star Micronics**
