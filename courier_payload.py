# courier_payload.py

def build_courier_payload(order_row: list) -> dict:
    return {
        "order_id": order_row[0],
        "pickup_address": "KITCHEN_ADDRESS",  # позже подтянем из Sheets кухни
        "dropoff_address": order_row[13] if len(order_row) > 13 else "",
        "pickup_eta_at": order_row[17] if len(order_row) > 17 else "",
        "customer": {
            "name": "",
            "phone": "",
        },
        "comment": order_row[7] if len(order_row) > 7 else "",
    }
