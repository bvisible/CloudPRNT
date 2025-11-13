#!/bin/bash
# Script pour clear le cache Frappe et résoudre le problème update_printers

echo "=========================================="
echo "🧹 CloudPRNT Cache Cleanup Script"
echo "=========================================="

echo ""
echo "Step 1: Clearing Frappe cache..."
bench --site your-site clear-cache

echo ""
echo "Step 2: Restarting bench..."
bench restart

echo ""
echo "=========================================="
echo "✅ Cache cleared and bench restarted!"
echo "=========================================="
echo ""
echo "📝 Next steps:"
echo "1. Hard refresh your browser: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac)"
echo "2. Go to CloudPRNT Settings"
echo "3. The error should be gone!"
echo ""
echo "If the error persists:"
echo "- Try clearing browser cache completely"
echo "- Try in an incognito/private window"
echo "- Check browser console (F12) for errors"
echo ""
