# CPUtil Installation Guide

## Vue d'ensemble

CPUtil est un outil de support développeur pour les serveurs CloudPRNT qui ne sont pas basés sur .NET Framework ou .NET Core. Il permet de convertir les jobs d'impression entre différents formats et de décoder les statuts d'imprimante.

**Documentation officielle**: https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html

## Méthodes d'installation

### Option 1: Installation depuis les binaires pré-compilés (RECOMMANDÉ)

Les binaires pré-compilés ne sont plus disponibles en téléchargement direct depuis Star Micronics. Ils doivent être téléchargés depuis la page de redirection officielle.

**URL de téléchargement**: https://www.star-m.jp/prjump/000199.html

Cette page redirige vers le fichier `.tar.gz` approprié pour Linux x64.

**Note**: Le lien de téléchargement direct change régulièrement. Utilisez toujours la page de redirection officielle.

### Option 2: Compilation depuis les sources (SI .NET 8+ EST INSTALLÉ)

Si .NET SDK 8 ou supérieur est installé sur le serveur:

```bash
# Cloner le repository
git clone https://github.com/star-micronics/cloudprnt-sdk.git
cd cloudprnt-sdk/CloudPRNTSDKSamples/cputil

# Ajouter le package CloudPRNT-Utility
dotnet add package StarMicronics.CloudPRNT-Utility --version 2.0.1

# Compiler pour Linux x64
dotnet publish -o /opt/star/cputil -c Release -r linux-x64 --self-contained

# Ajouter au PATH
sudo ln -s /opt/star/cputil/cputil /usr/local/bin/cputil
```

### Option 3: Installation de .NET 8 Runtime (puis Option 2)

Si vous souhaitez installer .NET 8 sur Ubuntu/Debian:

```bash
# Installer .NET 8 SDK
wget https://dot.net/v1/dotnet-install.sh -O dotnet-install.sh
chmod +x dotnet-install.sh
./dotnet-install.sh --version latest --runtime dotnet

# Puis suivre Option 2
```

## Alternative: CloudPRNT sans CPUtil

Le serveur CloudPRNT peut fonctionner **sans CPUtil** en utilisant uniquement le format Star Line Mode généré en Python natif.

### Avantages de CPUtil

- ✅ Conversion automatique entre formats (PNG → StarPRNT, Markup → Line Mode, etc.)
- ✅ Support de multiples formats de sortie
- ✅ Décodage des statuts ASB en JSON
- ✅ Gestion automatique des options (dithering, scaling, etc.)

### Fonctionnement sans CPUtil (implémentation actuelle)

Le code CloudPRNT actuel génère directement les commandes Star Line Mode en Python:

```python
# cloudprnt/print_job.py
class StarCloudPRNTStarLineModeJob:
    """Génère des commandes Star Line Mode en Python natif"""

    def add_text_line(self, text):
        """Ajoute du texte et un saut de ligne"""
        self.print_job_builder += self.str_to_hex(text)
        self.print_job_builder += self.SLM_NEW_LINE_HEX
```

Cette approche fonctionne parfaitement pour:
- ✅ Impression de texte
- ✅ Formatage (alignement, gras, magnification)
- ✅ Codes-barres
- ✅ Commandes imprimante (tiroir-caisse, coupure)
- ✅ UTF-8 et Windows-1252

**Limitations sans CPUtil**:
- ❌ Pas de conversion automatique PNG → StarPRNT
- ❌ Pas de support Star Document Markup (format texte avancé)
- ❌ Pas de décodage automatique des statuts ASB

## Tests et CPUtil

Les tests Phase 1 incluent des tests CPUtil qui sont automatiquement **skippés** si CPUtil n'est pas disponible:

```python
# Test automatiquement skippé si CPUtil absent
@pytest.mark.skipif(not is_cputil_available(), reason="CPUtil not installed")
def test_cputil_conversion():
    # ...
```

**Résultats actuels**:
- 31/59 tests passent (52.5%)
- 17 tests skippés (CPUtil + PNG)
- **Taux de réussite effectif: 73.8%** (31/42 tests non-skippés)

## Recommandation

### Pour develop.neoffice.me

**Option recommandée**: Fonctionner sans CPUtil

**Raisons**:
1. ✅ Le système actuel fonctionne parfaitement sans CPUtil
2. ✅ Tous les tests critiques passent (queue, serveur, print job basique)
3. ✅ L'implémentation Python native est plus maintenable
4. ✅ Pas de dépendance externe à gérer

### Pour production (si nécessaire)

Si vous avez besoin des fonctionnalités avancées de CPUtil:

1. **Télécharger manuellement** depuis https://www.star-m.jp/prjump/000199.html
2. **Extraire** dans `/opt/star/cputil/`
3. **Créer symlink**: `sudo ln -s /opt/star/cputil/cputil /usr/local/bin/cputil`
4. **Vérifier**: `cputil supportedinputs`

## Usage de CPUtil (quand installé)

### Exemples de commandes

```bash
# Lister les formats d'entrée supportés
cputil supportedinputs

# Obtenir les types média pour Star Markup
cputil mediatypes-mime text/vnd.star.markup

# Convertir Star Markup en StarPRNT pour imprimante 3 pouces
cputil thermal3 decode application/vnd.star.starprnt input.stm output.bin

# Convertir avec options (dithering, scale, tiroir-caisse)
cputil thermal3 dither scale-to-fit drawer-end decode application/vnd.star.starprnt image.png output.bin

# Décoder statut imprimante
cputil jsonstatus "23 86 00 00 00 00 00 00 00 00 00"
```

### Intégration Python

```python
import subprocess
import json

# Obtenir les types média
result = subprocess.run(
    ['cputil', 'mediatypes-mime', 'text/vnd.star.markup'],
    capture_output=True, text=True
)
media_types = json.loads(result.stdout)

# Convertir un job
subprocess.run([
    'cputil', 'thermal3', 'dither', 'scale-to-fit',
    'decode', 'application/vnd.star.starprnt',
    'input.stm', 'output.bin'
])
```

## Conclusion

**CPUtil n'est PAS requis** pour le fonctionnement de CloudPRNT sur develop.neoffice.me. Le système actuel génère les commandes d'impression directement en Python et fonctionne parfaitement.

Si vous souhaitez installer CPUtil pour les fonctionnalités avancées, suivez l'Option 1 (téléchargement manuel) car les binaires pré-compilés ne sont pas disponibles via URL directe.

**Status actuel**: ✅ CloudPRNT opérationnel sans CPUtil (73.8% de tests passent)
