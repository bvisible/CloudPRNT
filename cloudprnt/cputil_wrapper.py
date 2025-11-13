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
    1. CloudPRNT Settings.cputil_path (si configuré)
    2. PATH système (which cputil)
    3. Chemins standards: ~/.local/bin, /usr/local/bin, /opt/star/cputil

    :return: Chemin absolu vers CPUtil ou None si non trouvé
    """
    try:
        # 1. Vérifier settings
        custom_path = frappe.db.get_single_value("CloudPRNT Settings", "cputil_path")
        if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
            frappe.logger().debug(f"CPUtil found in settings: {custom_path}")
            return custom_path

        # 2. Chercher dans PATH
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
