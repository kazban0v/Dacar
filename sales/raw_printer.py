try:
    import win32print
    HAS_WIN32PRINT = True
except ImportError:
    win32print = None
    HAS_WIN32PRINT = False

from django.conf import settings

# --- ESC/POS Command Constants ---
ESC_INIT = b'\x1b\x40'                 # Initialize printer
ESC_ALIGN_LEFT = b'\x1b\x61\x00'      # Align left
ESC_ALIGN_CENTER = b'\x1b\x61\x01'    # Align center
ESC_ALIGN_RIGHT = b'\x1b\x61\x02'     # Align right

ESC_BOLD_ON = b'\x1b\x45\x01'         # Bold font ON
ESC_BOLD_OFF = b'\x1b\x45\x00'        # Bold font OFF

# Text Size (GS ! n)
GS_TEXT_NORMAL = b'\x1d\x21\x00'      # Normal size
GS_TEXT_DOUBLE_HEIGHT = b'\x1d\x21\x01' # Double height
GS_TEXT_DOUBLE_WIDTH = b'\x1d\x21\x10'  # Double width
GS_TEXT_LARGE = b'\x1d\x21\x11'       # Double height + width

# Code Table Selection
ESC_SELECT_CP866 = b'\x1b\x74\x07'     # Select CP866 (Cyrillic #2)
ESC_SELECT_CP866_ALT = b'\x1b\x74\x11' # Alternative CP866 selector

# Cut paper command (GS V 66 n) - Full cut / Partial cut
GS_CUT_FULL = b'\x1d\x56\x41\x00'     # Cut paper with 0 feed
GS_CUT_FEED = b'\x1d\x56\x42\x30'     # Feed paper then cut

def get_cups_default_printer():
    import subprocess
    import shutil
    if not shutil.which("lpstat"):
        return None
    try:
        # lpstat -d returns: "system default destination: Printer_Name"
        res = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, check=True)
        out = res.stdout.strip()
        if ":" in out:
            return out.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        # Alternatively, list first available printer from lpstat -p
        res = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            # line is like "printer Printer_Name is idle..."
            parts = line.split()
            if len(parts) > 1 and parts[0] == "printer":
                return parts[1]
    except Exception:
        pass
    return None

def get_target_printer_name():
    """
    Get target thermal printer name from settings or auto-detect ESC/POS printer or Windows default.
    """
    configured_name = getattr(settings, 'THERMAL_PRINTER_NAME', None)
    if configured_name:
        return configured_name
        
    if not HAS_WIN32PRINT:
        cups_printer = get_cups_default_printer()
        if cups_printer:
            return cups_printer
        return "Mock Printer (macOS)"
        
    try:
        printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        esc_printers = [p for p in printers if 'ESC' in p.upper() or 'POS' in p.upper()]
        if esc_printers:
            return esc_printers[0]
    except Exception:
        pass

    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return "Default Printer"



def build_escpos_bytes_for_order(order, shop_info=None):
    """
    Build raw ESC/POS byte sequence for a SaleOrder model instance.
    Formats text into 48 columns (standard 80mm thermal receipt width).
    """
    if shop_info is None:
        shop_info = {
            'name': getattr(settings, 'SHOP_NAME', 'DACAR ДЕТЕЙЛИНГ МАРКЕТ'),
            'tagline': getattr(settings, 'SHOP_TAGLINE', ''),
            'address': getattr(settings, 'SHOP_ADDRESS', 'г. Актобе, ул. Алтын Орда 19д'),
            'phone': getattr(settings, 'SHOP_PHONE', '+7 (706) 806-66-36'),
        }

    raw = bytearray()
    
    # 1. Initialize & Select Code Page 866 (Russian)
    raw.extend(ESC_INIT)
    raw.extend(ESC_SELECT_CP866)
    raw.extend(ESC_SELECT_CP866_ALT)

    def add_line(text="", align=ESC_ALIGN_LEFT, bold=False, size=GS_TEXT_NORMAL):
        raw.extend(align)
        raw.extend(size)
        if bold:
            raw.extend(ESC_BOLD_ON)
        else:
            raw.extend(ESC_BOLD_OFF)
        
        # Encode string to CP866 bytes
        encoded = (text + "\n").encode('cp866', errors='replace')
        raw.extend(encoded)

    # 2. Header
    add_line(shop_info['name'], align=ESC_ALIGN_CENTER, bold=True, size=GS_TEXT_LARGE)
    if shop_info['tagline']:
        add_line(shop_info['tagline'], align=ESC_ALIGN_CENTER)
    add_line(shop_info['address'], align=ESC_ALIGN_CENTER)
    add_line(f"Тел: {shop_info['phone']}", align=ESC_ALIGN_CENTER)
    add_line("-" * 48, align=ESC_ALIGN_CENTER)

    # 3. Meta information
    cashier_name = order.cashier.get_full_name() if order.cashier else 'Админ'
    if not cashier_name and order.cashier:
        cashier_name = order.cashier.username
        
    date_str = order.created_at.strftime('%d.%m.%Y %H:%M:%S')
    payment_str = order.get_payment_method_display() if hasattr(order, 'get_payment_method_display') else str(order.payment_method)

    # Left-Right formatted metadata lines (48 chars total)
    def add_pair(left, right):
        # Format left and right to fit 48 characters width
        left = str(left)
        right = str(right)
        space_len = max(1, 48 - len(left) - len(right))
        line = left + (" " * space_len) + right
        add_line(line, align=ESC_ALIGN_LEFT)

    add_pair("Чек №", order.order_number)
    add_pair("Дата", date_str)
    add_pair("Кассир", cashier_name)
    add_pair("Оплата", payment_str)
    add_line("-" * 48, align=ESC_ALIGN_CENTER)

    # 4. Table Header (Width: 28 chars name | 6 chars qty | 12 chars sum)
    # Total: 28 + 2 + 6 + 2 + 10 = 48
    header_col = f"{'НАИМЕНОВАНИЕ':<28} {'КОЛ':^6} {'СУММА':>12}"
    add_line(header_col, align=ESC_ALIGN_LEFT, bold=True)
    add_line("-" * 48, align=ESC_ALIGN_CENTER)

    # 5. Order Items
    for item in order.items.all():
        name = item.product.name if hasattr(item, 'product') and item.product else 'Товар'
        qty_str = f"{item.quantity:.3f}" if isinstance(item.quantity, float) or '.' in str(item.quantity) else f"{int(item.quantity)}"
        price_str = f"{int(item.total_amount):,} Т".replace(',', ' ')

        
        # If item name is long, wrap it cleanly across lines
        if len(name) > 28:
            first_line_name = name[:28]
            add_line(f"{first_line_name:<28} {qty_str:^6} {price_str:>12}")
            remaining_name = name[28:]
            while remaining_name:
                add_line(remaining_name[:48])
                remaining_name = remaining_name[48:]
        else:
            add_line(f"{name:<28} {qty_str:^6} {price_str:>12}")

    add_line("-" * 48, align=ESC_ALIGN_CENTER)

    # 6. Totals
    subtotal_str = f"{int(order.subtotal_amount):,} Т".replace(',', ' ')
    discount_str = f"{int(order.discount_amount):,} Т".replace(',', ' ')
    total_str = f"{int(order.total_amount):,} Т".replace(',', ' ')
    paid_str = f"{int(order.paid_amount):,} Т".replace(',', ' ')
    change_str = f"{int(order.change_amount):,} Т".replace(',', ' ')


    add_pair("Подытог", subtotal_str)
    add_pair("Скидка", discount_str)
    
    # Grand Total Large Bold
    add_line("=" * 48, align=ESC_ALIGN_CENTER)
    add_pair("ИТОГО К ОПЛАТЕ:", total_str)
    add_line("=" * 48, align=ESC_ALIGN_CENTER)

    add_pair("Внесено", paid_str)
    add_pair("Сдача", change_str)
    add_line("-" * 48, align=ESC_ALIGN_CENTER)

    # 7. Footer
    add_line("Спасибо за покупку в DACAR!", align=ESC_ALIGN_CENTER, bold=True)
    add_line("Сохраняйте чек для возврата — 14 дней", align=ESC_ALIGN_CENTER)
    
    # 8. Extra feed lines & Auto-cut
    raw.extend(b"\n\n\n\n")
    raw.extend(GS_CUT_FULL)

    return bytes(raw)


def send_raw_bytes_to_printer(raw_data, printer_name=None):
    """
    Send raw ESC/POS bytes directly to printer.
    Supports Windows (via win32print) and macOS/Linux (via CUPS lpr).
    """
    if not printer_name:
        printer_name = get_target_printer_name()

    if not HAS_WIN32PRINT:
        import shutil
        if shutil.which("lpr"):
            try:
                import tempfile
                import subprocess
                import os
                # Write raw bytes to a temporary file
                with tempfile.NamedTemporaryFile(delete=False) as f:
                    f.write(raw_data)
                    temp_name = f.name

                if printer_name in ["Mock Printer (macOS)", "Default Printer"]:
                    print(f"[INFO] Mock print simulated for macOS: {len(raw_data)} bytes generated successfully.")
                    os.unlink(temp_name)
                    return True

                cmd = ["lpr", "-P", printer_name, "-o", "raw", temp_name]
                res = subprocess.run(cmd, capture_output=True, text=True)
                os.unlink(temp_name)
                
                if res.returncode == 0:
                    print(f"[INFO] Sent raw ESC/POS print job to macOS printer '{printer_name}'")
                    return True
                else:
                    print(f"[WARNING] lpr printer command returned code {res.returncode}: {res.stderr.strip()}")
                    print("[INFO] Fallback to simulated print logging so browser dialog is not forced.")
                    return True
            except Exception as e:
                print(f"[ERROR] Printer exception on macOS: {e}")
                return True
        else:
            print("[WARNING] Printing is mock-simulated because win32print and lpr are not available.")
            print(f"Target Printer: {printer_name or 'Default'}")
            print(f"Raw Data Size: {len(raw_data)} bytes")
            return True

    h_printer = win32print.OpenPrinter(printer_name)
    try:
        h_job = win32print.StartDocPrinter(h_printer, 1, ("DACAR ESC/POS Receipt", None, "RAW"))
        win32print.StartPagePrinter(h_printer)
        win32print.WritePrinter(h_printer, raw_data)
        win32print.EndPagePrinter(h_printer)
        win32print.EndDocPrinter(h_printer)
        return True
    finally:
        win32print.ClosePrinter(h_printer)


def print_order_direct(order, printer_name=None):
    """
    High-level function to print a SaleOrder directly to thermal printer.
    """
    raw_bytes = build_escpos_bytes_for_order(order)
    return send_raw_bytes_to_printer(raw_bytes, printer_name=printer_name)
