# utils/csv_importer.py
import csv, io
from .parsers import parse_price
from .csv_mapping import CSV_FIELD_ALIASES
from ..models import Product
from decimal import Decimal

def match_field(header_name):
    """Try to find which model field this header corresponds to."""
    header_name = header_name.strip().lower()
    for field, aliases in CSV_FIELD_ALIASES.items():
        if header_name in aliases:
            return field
    return None  # Unrecognized column

def import_products_from_csv(file, user):
    decoded_file = file.read().decode('utf-8').splitlines()

    if not decoded_file or len(decoded_file) < 2:
        return {"status": "error", "message": "CSV file is empty or missing headers."}

    reader = csv.DictReader(decoded_file)

    if not reader.fieldnames:
        return {"status": "error", "message": "No headers detected in CSV file."}

    header_map = {}
    for h in reader.fieldnames:
        matched = match_field(h)
        if matched:
            header_map[h] = matched

    if not header_map:
        return {"status": "error", "message": "No recognizable headers found in CSV."}

    required_fields = ["name", "sku", "stock", "unit_price"]
    created = []
    errors = []

    for row_index, row in enumerate(reader, start=1):
        product_data = {}
        for csv_field, model_field in header_map.items():
            value = row.get(csv_field, "").strip() if row.get(csv_field) else None
            product_data[model_field] = value

        missing = [f for f in required_fields if not product_data.get(f)]
        if missing:
            errors.append(f"Row {row_index}: Missing required fields: {', '.join(missing)}")
            continue

        try:
            product_data["stock"] = int(product_data.get("stock", 0))
            product_data["unit_price"] = parse_price(product_data.get("unit_price", "0"))
        except ValueError as e:
            errors.append(f"Row {row_index}: {e}")
            continue

        try:
            product = Product.objects.create(**product_data, user=user)
            created.append(product)
        except Exception as e:
            errors.append(f"Row {row_index}: {str(e)}")

    status = "success" if not errors else "partial"
    return {"status": status, "created": len(created), "errors": errors}