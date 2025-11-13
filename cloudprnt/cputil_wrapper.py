"""
CPUtil Wrapper for CloudPRNT
=============================

Wrapper Python pour l'outil officiel Star Micronics CPUtil.
Permet la conversion de Star Document Markup vers Star Line Mode
avec fallback automatique vers la génération Python native.

CPUtil Documentation:
https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html
"""

import os
import subprocess
import tempfile
import shutil
import frappe
from frappe import _
import binascii


# Mapping des largeurs d'imprimante vers les options CPUtil
PRINTER_WIDTH_MAP = {
    2: 'thermal2',    # 58mm / 2 inch - 384 dots
    3: 'thermal3',    # 80mm / 3 inch - 576 dots (default)
    4: 'thermal4',    # 112mm / 4 inch - 832 dots
}


def get_cputil_path():
    """
    Trouve le chemin du binaire CPUtil

    Ordre de recherche:
    1. Binaire embarqué dans l'app CloudPRNT (cloudprnt/bin/cputil)
    2. CloudPRNT Settings.cputil_path (si configuré)
    3. PATH système (which cputil)
    4. Chemins standards: ~/.local/bin, /usr/local/bin, /opt/star/cputil

    :return: Chemin absolu vers CPUtil ou None si non trouvé
    """
    try:
        # 1. Vérifier binaire embarqué dans l'app
        app_dir = os.path.dirname(os.path.abspath(__file__))
        embedded_cputil = os.path.join(app_dir, 'bin', 'cputil')
        if os.path.isfile(embedded_cputil) and os.access(embedded_cputil, os.X_OK):
            frappe.logger().debug(f"CPUtil found in app: {embedded_cputil}")
            return embedded_cputil

        # 2. Vérifier settings
        custom_path = frappe.db.get_single_value("CloudPRNT Settings", "cputil_path")
        if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
            frappe.logger().debug(f"CPUtil found in settings: {custom_path}")
            return custom_path

        # 3. Chercher dans PATH
        result = shutil.which("cputil")
        if result:
            frappe.logger().debug(f"CPUtil found in PATH: {result}")
            return result

        # 3. Chemins standards
        standard_paths = [
            os.path.expanduser("~/.local/bin/cputil"),
            "/usr/local/bin/cputil",
            "/opt/star/cputil/cputil",
            "/usr/bin/cputil",
        ]

        for path in standard_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                frappe.logger().debug(f"CPUtil found at: {path}")
                return path

        frappe.logger().debug("CPUtil not found in any standard location")
        return None

    except Exception as e:
        frappe.logger().error(f"Error finding CPUtil: {str(e)}")
        return None


def is_cputil_available():
    """
    Vérifie si CPUtil est disponible et fonctionnel

    Teste en exécutant: cputil supportedinputs

    :return: True si CPUtil est disponible et fonctionne, False sinon
    """
    try:
        cputil_path = get_cputil_path()
        if not cputil_path:
            return False

        # Tester avec la commande supportedinputs
        result = subprocess.run(
            [cputil_path, "supportedinputs"],
            capture_output=True,
            text=True,
            timeout=5
        )

        # Si succès et contient "text/vnd.star.markup"
        if result.returncode == 0 and "text/vnd.star.markup" in result.stdout:
            frappe.logger().debug("CPUtil is available and functional")
            return True

        frappe.logger().warning(f"CPUtil test failed: {result.stderr}")
        return False

    except subprocess.TimeoutExpired:
        frappe.logger().error("CPUtil test timed out")
        return False
    except Exception as e:
        frappe.logger().error(f"Error testing CPUtil: {str(e)}")
        return False


def get_supported_input_types():
    """
    Récupère la liste des types d'entrée supportés par CPUtil

    :return: Liste des types MIME supportés ou None si erreur
    """
    try:
        cputil_path = get_cputil_path()
        if not cputil_path:
            return None

        result = subprocess.run(
            [cputil_path, "supportedinputs"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            # Parse la sortie (format: une ligne par type MIME)
            types = [line.strip() for line in result.stdout.strip().split('\n')]
            return types

        return None

    except Exception as e:
        frappe.logger().error(f"Error getting supported input types: {str(e)}")
        return None


def build_cputil_command(options=None):
    """
    Construit la liste d'arguments pour la commande CPUtil

    :param options: Dict d'options (voir PRINTER_WIDTH_MAP et autres)
    :return: Liste d'arguments pour subprocess
    """
    if options is None:
        options = {}

    cputil_path = get_cputil_path()
    if not cputil_path:
        raise FileNotFoundError("CPUtil binary not found")

    cmd = [cputil_path]

    # Largeur imprimante (défaut: thermal3 = 80mm)
    printer_width = options.get('printer_width', 3)
    width_arg = PRINTER_WIDTH_MAP.get(printer_width, 'thermal3')
    cmd.append(width_arg)

    # Résolution 300 dpi
    if options.get('resolution_300dpi'):
        cmd.append('300dpi')

    # Magnification texte pour 300 dpi
    if options.get('text_mag_1_5x'):
        cmd.append('text-mag-1_5x')

    # Dithering image
    if options.get('dither', True):  # Activé par défaut
        cmd.append('dither')

    # Scale to fit
    if options.get('scale_to_fit', False):
        cmd.append('scale-to-fit')

    # Cash drawer
    drawer = options.get('drawer', 'none')
    if drawer == 'start':
        cmd.append('drawer-start')
    elif drawer == 'end':
        cmd.append('drawer-end')
    # 'none' = pas d'argument (défaut)

    # Buzzer
    buzzer_start = options.get('buzzer_start')
    if buzzer_start and buzzer_start > 0:
        cmd.extend(['buzzer-start', str(int(buzzer_start))])

    buzzer_end = options.get('buzzer_end')
    if buzzer_end and buzzer_end > 0:
        cmd.extend(['buzzer-end', str(int(buzzer_end))])

    # Type de coupe
    if options.get('partial_cut', True):  # Partial cut par défaut
        cmd.append('partialcut')
    else:
        cmd.append('fullcut')

    # UTF-8 (par défaut)
    if not options.get('sbcs_only', False):
        cmd.append('utf8')

    return cmd


def convert_markup_to_starline(markup_text, options=None):
    """
    Convertit Star Document Markup vers Star Line Mode (hex)

    Utilise CPUtil avec stdin/stdout pour éviter les fichiers temporaires.
    Commande: cputil [options] decode application/vnd.star.line - [stdout]

    :param markup_text: Texte en format Star Document Markup
    :param options: Dict d'options (printer_width, dither, etc.)
    :return: String hex (uppercase) du job Star Line Mode
    :raises: Exception si conversion échoue
    """
    try:
        # Construire la commande
        cmd = build_cputil_command(options)

        # Ajouter la commande decode
        cmd.extend([
            'decode',
            'application/vnd.star.line',  # Format de sortie
            '-',                          # Input depuis stdin
            '[stdout]'                    # Output vers stdout
        ])

        frappe.logger().debug(f"CPUtil command: {' '.join(cmd)}")

        # Exécuter avec timeout de 30 secondes
        result = subprocess.run(
            cmd,
            input=markup_text.encode('utf-8'),  # Envoyer markup via stdin
            capture_output=True,
            timeout=30
        )

        # Vérifier le code de retour
        if result.returncode != 0:
            error_msg = result.stderr.decode('utf-8', errors='replace')
            frappe.logger().error(f"CPUtil conversion failed: {error_msg}")
            raise Exception(f"CPUtil returned error code {result.returncode}: {error_msg}")

        # Convertir la sortie binaire en hex uppercase
        binary_output = result.stdout
        hex_output = binascii.hexlify(binary_output).decode('ascii').upper()

        frappe.logger().info(f"CPUtil conversion successful: {len(hex_output)} hex chars")

        return hex_output

    except subprocess.TimeoutExpired:
        error_msg = "CPUtil conversion timed out after 30 seconds"
        frappe.logger().error(error_msg)
        raise Exception(error_msg)

    except Exception as e:
        frappe.logger().error(f"Error in CPUtil conversion: {str(e)}")
        raise


def convert_image_to_starline(image_path, options=None):
    """
    Convertit une image (PNG/JPEG/BMP/GIF) vers Star Line Mode

    :param image_path: Chemin vers le fichier image
    :param options: Dict d'options (printer_width, dither, scale_to_fit, etc.)
    :return: String hex du job Star Line Mode
    :raises: Exception si conversion échoue
    """
    try:
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        # Construire la commande
        cmd = build_cputil_command(options)

        # Ajouter la commande decode
        cmd.extend([
            'decode',
            'application/vnd.star.line',
            image_path,
            '[stdout]'
        ])

        frappe.logger().debug(f"CPUtil image command: {' '.join(cmd)}")

        # Exécuter
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30
        )

        if result.returncode != 0:
            error_msg = result.stderr.decode('utf-8', errors='replace')
            raise Exception(f"CPUtil image conversion failed: {error_msg}")

        # Convertir en hex
        hex_output = binascii.hexlify(result.stdout).decode('ascii').upper()

        frappe.logger().info(f"CPUtil image conversion successful: {len(hex_output)} hex chars")

        return hex_output

    except subprocess.TimeoutExpired:
        raise Exception("CPUtil image conversion timed out after 30 seconds")
    except Exception as e:
        frappe.logger().error(f"Error in CPUtil image conversion: {str(e)}")
        raise


@frappe.whitelist()
def check_cputil_status():
    """
    API endpoint pour vérifier le status de CPUtil
    Utilisé par l'interface CloudPRNT Settings

    :return: Dict avec status, path, version
    """
    try:
        cputil_path = get_cputil_path()

        if not cputil_path:
            return {
                "available": False,
                "status": "❌ Not Found",
                "message": "CPUtil binary not found. Please install CPUtil or configure the path.",
                "path": None
            }

        # Tester la version
        try:
            result = subprocess.run(
                [cputil_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            version = result.stdout.strip() if result.returncode == 0 else "Unknown"
        except:
            version = "Unknown"

        # Vérifier fonctionnalité
        if is_cputil_available():
            return {
                "available": True,
                "status": "✅ Available",
                "message": f"CPUtil is installed and functional",
                "path": cputil_path,
                "version": version
            }
        else:
            return {
                "available": False,
                "status": "⚠️ Found but not functional",
                "message": "CPUtil binary found but test failed",
                "path": cputil_path
            }

    except Exception as e:
        frappe.log_error(str(e), "CPUtil Status Check")
        return {
            "available": False,
            "status": "❌ Error",
            "message": str(e),
            "path": None
        }


def convert_png_to_starprnt(png_path, options=None):
    """
    Convertit une image PNG en commandes StarPRNT binaires via CPUtil
    
    Cette fonction utilise le binaire CPUtil embarqué dans l'app pour
    convertir des images PNG en format StarPRNT prêt à être envoyé
    aux imprimantes Star Micronics.
    
    :param png_path: Chemin vers le fichier PNG
    :param options: Dict d'options de conversion:
        - printer_width: int (2, 3, 4) ou str ('thermal2', 'thermal3', 'thermal4')
        - dither: bool - Appliquer dithering (défaut: True)
        - scale_to_fit: bool - Redimensionner pour ajuster (défaut: True)
        - drawer_end: bool - Ouvrir tiroir-caisse à la fin (défaut: False)
        - buzzer_end: int - Nombre de bips à la fin (défaut: 0)
        - partial_cut: bool - Coupe partielle (défaut: True)
    :return: bytes - Données binaires StarPRNT prêtes à envoyer
    :raises: Exception si CPUtil non disponible ou conversion échoue
    
    Exemple:
        >>> binary_data = convert_png_to_starprnt(
        ...     '/path/to/receipt.png',
        ...     options={'printer_width': 3, 'dither': True, 'drawer_end': True}
        ... )
        >>> # Envoyer binary_data à l'imprimante
    """
    if options is None:
        options = {}
    
    # Vérifier que CPUtil est disponible
    if not is_cputil_available():
        raise Exception(_("CPUtil n'est pas disponible. Le binaire embarqué est peut-être corrompu."))
    
    # Vérifier que le fichier PNG existe
    if not os.path.isfile(png_path):
        raise FileNotFoundError(_("Fichier PNG introuvable: {0}").format(png_path))
    
    try:
        # Construire la commande CPUtil
        cmd = build_cputil_command(options)
        
        # Ajouter la commande decode pour PNG → StarPRNT
        cmd.extend([
            'decode',
            'application/vnd.star.starprnt',  # Format de sortie StarPRNT
            png_path,                          # Fichier PNG en entrée
            '-'                                # Output vers stdout (binaire)
        ])
        
        frappe.logger().debug(f"Converting PNG to StarPRNT: {' '.join(cmd)}")
        
        # Exécuter CPUtil avec timeout de 30 secondes
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            check=False  # Ne pas lever exception automatiquement
        )
        
        # Vérifier le succès
        if result.returncode != 0:
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            frappe.logger().error(f"CPUtil PNG conversion failed: {error_msg}")
            raise Exception(_("Échec de conversion PNG: {0}").format(error_msg))
        
        # Retourner les données binaires
        binary_data = result.stdout
        
        frappe.logger().info(
            f"PNG converted to StarPRNT successfully: "
            f"{len(binary_data)} bytes from {os.path.basename(png_path)}"
        )
        
        return binary_data
        
    except subprocess.TimeoutExpired:
        frappe.logger().error(f"CPUtil PNG conversion timeout after 30s: {png_path}")
        raise Exception(_("Timeout lors de la conversion PNG (30s)"))
    
    except Exception as e:
        frappe.logger().error(f"PNG to StarPRNT conversion error: {str(e)}")
        raise


def convert_image_to_starprnt(image_path, options=None):
    """
    Convertit une image (PNG, JPEG, BMP, GIF) en StarPRNT via CPUtil
    
    Fonction générique qui détecte automatiquement le type d'image
    et utilise CPUtil pour la conversion en StarPRNT.
    
    :param image_path: Chemin vers le fichier image
    :param options: Options de conversion (voir convert_png_to_starprnt)
    :return: bytes - Données binaires StarPRNT
    
    Exemple:
        >>> binary = convert_image_to_starprnt('/path/to/logo.jpg')
    """
    # Cette fonction utilise la même logique que PNG
    # CPUtil supporte automatiquement PNG, JPEG, BMP, GIF
    return convert_png_to_starprnt(image_path, options)


# Fonctions de convenance pour largeurs d'imprimante standard

def convert_png_to_starprnt_58mm(png_path, **options):
    """Convertit PNG → StarPRNT pour imprimante 58mm/2" (mC-Print2)"""
    options['printer_width'] = 2
    return convert_png_to_starprnt(png_path, options)


def convert_png_to_starprnt_80mm(png_path, **options):
    """Convertit PNG → StarPRNT pour imprimante 80mm/3" (mC-Print3, TSP650II)"""
    options['printer_width'] = 3
    return convert_png_to_starprnt(png_path, options)


def convert_png_to_starprnt_112mm(png_path, **options):
    """Convertit PNG → StarPRNT pour imprimante 112mm/4" (TSP800II)"""
    options['printer_width'] = 4
    return convert_png_to_starprnt(png_path, options)
