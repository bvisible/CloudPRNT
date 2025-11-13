# CPUtil - Exemples d'Utilisation

CPUtil est maintenant **embarqué dans l'app CloudPRNT** - aucune installation nécessaire !

## 🎯 Conversion PNG → StarPRNT

### Exemple Simple

```python
from cloudprnt.cputil_wrapper import convert_png_to_starprnt_80mm

# Convertir une image PNG en commandes StarPRNT pour imprimante 80mm
binary_data = convert_png_to_starprnt_80mm(
    '/path/to/receipt.png',
    dither=True,
    scale_to_fit=True
)

# Enregistrer dans un fichier
with open('job.bin', 'wb') as f:
    f.write(binary_data)

# Ou envoyer directement à l'imprimante via CloudPRNT
```

### Exemple Complet avec Options

```python
from cloudprnt.cputil_wrapper import convert_png_to_starprnt

# Conversion avec toutes les options
binary_data = convert_png_to_starprnt(
    '/path/to/logo.png',
    options={
        'printer_width': 3,        # 3 pouces (80mm)
        'dither': True,            # Appliquer dithering pour meilleure qualité
        'scale_to_fit': True,      # Redimensionner pour ajuster à la largeur
        'drawer_end': True,        # Ouvrir tiroir-caisse à la fin
        'buzzer_end': 2,           # Sonner 2 fois à la fin
        'partial_cut': True        # Coupe partielle (défaut)
    }
)

print(f"Généré {len(binary_data)} bytes de données StarPRNT")
```

### Fonctions de Convenance par Taille d'Imprimante

```python
from cloudprnt.cputil_wrapper import (
    convert_png_to_starprnt_58mm,   # mC-Print2 (2 pouces)
    convert_png_to_starprnt_80mm,   # mC-Print3, TSP650II (3 pouces)
    convert_png_to_starprnt_112mm   # TSP800II (4 pouces)
)

# Pour imprimante 58mm (2 pouces)
data_58mm = convert_png_to_starprnt_58mm(
    'receipt.png',
    dither=True,
    drawer_end=True
)

# Pour imprimante 80mm (3 pouces) - PLUS COURANT
data_80mm = convert_png_to_starprnt_80mm(
    'receipt.png',
    dither=True,
    scale_to_fit=True
)

# Pour imprimante 112mm (4 pouces)
data_112mm = convert_png_to_starprnt_112mm(
    'receipt.png',
    dither=True
)
```

## 🖼️ Conversion Images (PNG, JPEG, BMP, GIF)

```python
from cloudprnt.cputil_wrapper import convert_image_to_starprnt

# Fonctionne avec tous les formats d'image
formats = ['logo.png', 'photo.jpg', 'banner.bmp', 'icon.gif']

for image_file in formats:
    binary_data = convert_image_to_starprnt(
        image_file,
        options={'printer_width': 3, 'dither': True}
    )
    print(f"{image_file}: {len(binary_data)} bytes")
```

## 📄 Intégration dans CloudPRNT API

### Dans api.py - Impression d'une Image

```python
@frappe.whitelist()
def print_image_to_cloudprnt(image_path, printer_mac, printer_width=3):
    """
    Imprime une image PNG/JPEG sur une imprimante CloudPRNT

    :param image_path: Chemin vers l'image
    :param printer_mac: Adresse MAC de l'imprimante
    :param printer_width: 2, 3, ou 4 (pouces)
    """
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt
    from cloudprnt.print_queue_manager import add_job_to_queue
    import uuid

    try:
        # Convertir l'image en StarPRNT
        binary_data = convert_png_to_starprnt(
            image_path,
            options={
                'printer_width': printer_width,
                'dither': True,
                'scale_to_fit': True,
                'partial_cut': True
            }
        )

        # Convertir en hex pour stockage
        hex_data = binary_data.hex().upper()

        # Ajouter à la queue d'impression
        job_token = f"IMG-{uuid.uuid4().hex[:8].upper()}"

        result = add_job_to_queue(
            job_token=job_token,
            printer_mac=printer_mac,
            job_data=hex_data,
            media_types=["application/vnd.star.starprnt"]
        )

        return {
            "success": True,
            "job_token": job_token,
            "message": f"Image ajoutée à la queue ({len(binary_data)} bytes)"
        }

    except Exception as e:
        frappe.log_error(f"Erreur impression image: {str(e)}", "print_image_to_cloudprnt")
        return {
            "success": False,
            "message": str(e)
        }
```

### Dans POS Invoice - Ajouter un Logo

```python
def add_company_logo_to_receipt(invoice_name, printer_mac):
    """
    Ajoute le logo de l'entreprise à un reçu POS
    """
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt_80mm
    import frappe

    # Récupérer le logo de l'entreprise
    company = frappe.get_doc("POS Invoice", invoice_name).company
    company_doc = frappe.get_doc("Company", company)

    if not company_doc.company_logo:
        return None

    # Chemin vers le logo
    logo_path = frappe.get_site_path('public', company_doc.company_logo.lstrip('/'))

    # Convertir le logo en StarPRNT
    logo_binary = convert_png_to_starprnt_80mm(
        logo_path,
        dither=True,
        scale_to_fit=True
    )

    return logo_binary
```

## 📊 Exemple Complet: Reçu avec Logo + Texte

```python
def create_receipt_with_logo(invoice_name, printer_mac):
    """
    Crée un reçu avec logo + contenu texte
    """
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt_80mm
    from cloudprnt.pos_invoice_markup import get_pos_invoice_markup
    from cloudprnt.print_job import StarCloudPRNTStarLineModeJob
    from cloudprnt.print_queue_manager import add_job_to_queue
    import uuid

    # 1. Générer le logo (en haut)
    logo_binary = convert_png_to_starprnt_80mm(
        '/path/to/company_logo.png',
        dither=True,
        scale_to_fit=True
    )

    # 2. Générer le contenu texte du reçu
    markup = get_pos_invoice_markup(invoice_name)

    printer_meta = {'printerMAC': printer_mac.replace(':', '.')}
    star_job = StarCloudPRNTStarLineModeJob(printer_meta)

    # Parser le markup et construire le job texte
    lines = markup.split('\n')
    for line in lines:
        if '[cut' not in line:  # Ne pas couper encore
            # Traiter les tags d'alignement, magnification, etc.
            star_job.add_text_line(line)

    # Couper à la fin
    star_job.cut()

    # Convertir le job texte en binaire
    text_hex = star_job.print_job_builder
    text_binary = bytes.fromhex(text_hex)

    # 3. Combiner logo + texte
    combined_binary = logo_binary + text_binary

    # 4. Ajouter à la queue
    job_token = f"LOGO-{uuid.uuid4().hex[:8].upper()}"

    add_job_to_queue(
        job_token=job_token,
        printer_mac=printer_mac,
        job_data=combined_binary.hex().upper(),
        media_types=["application/vnd.star.starprnt"]
    )

    return job_token
```

## ⚙️ Options de Conversion Disponibles

### printer_width
- `2` ou `'thermal2'` ou `'thermal58'` - 58mm / 2 pouces (384 dots) - mC-Print2
- `3` ou `'thermal3'` ou `'thermal80'` - 80mm / 3 pouces (576 dots) - **mC-Print3, TSP650II** ⭐
- `4` ou `'thermal4'` ou `'thermal112'` - 112mm / 4 pouces (832 dots) - TSP800II

### dither
- `True` - Applique dithering Floyd-Steinberg pour meilleure qualité (recommandé)
- `False` - Pas de dithering

### scale_to_fit
- `True` - Redimensionne l'image pour ajuster à la largeur d'impression (recommandé)
- `False` - Garde la taille originale (peut être coupée si trop large)

### drawer_end
- `True` - Ouvre le tiroir-caisse à la fin de l'impression
- `False` - N'ouvre pas le tiroir

### buzzer_end
- `0` - Pas de bip (défaut)
- `1-9` - Nombre de bips à la fin de l'impression

### partial_cut
- `True` - Coupe partielle (défaut, recommandé)
- `False` - Coupe complète

## 🔍 Vérification CPUtil

```python
from cloudprnt.cputil_wrapper import (
    is_cputil_available,
    get_cputil_path,
    check_cputil_status
)

# Vérifier disponibilité
if is_cputil_available():
    print("✅ CPUtil disponible")
    print(f"Chemin: {get_cputil_path()}")
else:
    print("❌ CPUtil non disponible")

# Obtenir le statut complet
status = check_cputil_status()
print(f"Version: {status.get('version')}")
print(f"Formats supportés: {status.get('supported_inputs')}")
```

## 🚀 Cas d'Usage Réels

### 1. Logo d'Entreprise sur Chaque Reçu

```python
# Dans hooks.py - déclenché avant chaque impression
def before_print_pos_invoice(doc, method):
    """Hook appelé avant impression POS"""
    if doc.print_logo:
        # Ajouter le logo automatiquement
        add_company_logo_to_receipt(doc.name, doc.printer_mac)
```

### 2. QR Code sur Reçu

```python
import qrcode
from io import BytesIO

def add_qr_code_to_receipt(invoice_name, printer_mac):
    """Ajoute un QR code de l'URL de la facture"""

    # Générer QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(f"https://monsite.com/invoice/{invoice_name}")
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Sauvegarder temporairement
    temp_file = f"/tmp/qr_{invoice_name}.png"
    img.save(temp_file)

    # Convertir en StarPRNT
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt_80mm

    qr_binary = convert_png_to_starprnt_80mm(
        temp_file,
        dither=False,  # QR code pas besoin de dithering
        scale_to_fit=False  # Garder taille originale pour lisibilité
    )

    return qr_binary
```

### 3. Image Promotionnelle

```python
def print_promotional_image(image_url, printer_mac):
    """Imprime une image promotionnelle"""
    import requests

    # Télécharger l'image
    response = requests.get(image_url)
    temp_file = f"/tmp/promo_{frappe.generate_hash()}.png"

    with open(temp_file, 'wb') as f:
        f.write(response.content)

    # Convertir et imprimer
    from cloudprnt.cputil_wrapper import convert_png_to_starprnt_80mm

    binary_data = convert_png_to_starprnt_80mm(
        temp_file,
        dither=True,
        scale_to_fit=True
    )

    # Ajouter à la queue
    from cloudprnt.print_queue_manager import add_job_to_queue

    add_job_to_queue(
        job_token=f"PROMO-{frappe.generate_hash()[:8]}",
        printer_mac=printer_mac,
        job_data=binary_data.hex().upper(),
        media_types=["application/vnd.star.starprnt"]
    )
```

## ✅ Avantages

- ✅ **Aucune installation nécessaire** - CPUtil embarqué dans l'app
- ✅ **Fonctionne out-of-the-box** après `bench install-app cloudprnt`
- ✅ **Support multi-formats** - PNG, JPEG, BMP, GIF
- ✅ **Qualité optimale** - Dithering et scaling automatiques
- ✅ **API Python simple** - Pas besoin de gérer subprocess
- ✅ **Testé et validé** par Star Micronics

## 📚 Documentation Complète

Voir documentation officielle CPUtil:
https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html
