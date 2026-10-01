"""Genera observaciones de mercado SINTÉTICAS para la demostración del Agente Central.

No son datos reales del mercado cubano: sirven para ejecutar el sistema completo sin conexión a las webs.
Las fuentes se llaman ``demo_*`` y sus URLs usan el dominio reservado ``ejemplo.invalid``, para que nunca
se confundan con fuentes reales. El resultado es determinista (misma semilla → mismos archivos).

Cada producto sigue una historia coherente con las situaciones sembradas en el negocio de demostración
(aceite agotado con el mercado escaso, café con margen bajo y el mercado al alza, ventiladores en exceso
con precios a la baja al acabar el verano…).

    python demo/mercado/generar_mercado_demo.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SALIDA = Path(__file__).resolve().parent / "observaciones"
FIN = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
SEMANAS = 12
# Tasa informal USD→CUP aproximada de cada semana (para fijar los precios en CUP de los clasificados).
TASA = [670, 668, 672, 674, 675, 670, 668, 668, 675, 682, 700, 735]

# producto: títulos posibles, precio USD equivalente (inicio → fin), anuncios por semana (inicio → fin),
# cuota de anuncios en tiendas online (USD) frente a clasificados (CUP).
PRODUCTOS = {
    "aceite": {"titulos": ["Aceite de girasol 1 L", "Aceite girasol botella 1L", "Aceite de girasol 1 litro"],
               "precio": (3.95, 4.95), "anuncios": (15, 6), "tiendas": 0.35},
    "leche": {"titulos": ["Leche en polvo 1 kg", "Leche en polvo entera bolsa 1kg", "Leche polvo 1 kg"],
              "precio": (10.70, 10.95), "anuncios": (13, 12), "tiendas": 0.4},
    "cafe": {"titulos": ["Café molido 250 g", "Cafe molido paquete 250g", "Café puro molido 250 g"],
             "precio": (4.60, 5.65), "anuncios": (12, 11), "tiendas": 0.35},
    "huevos": {"titulos": ["Huevos cartón 30 u", "Cartón de huevos 30 unidades", "Huevos 30 u"],
               "precio": (9.30, 10.75), "anuncios": (12, 12), "tiendas": 0.3},
    "arroz": {"titulos": ["Arroz 1 kg", "Arroz blanco 1 kg", "Arroz bolsa 1kg"],
              "precio": (2.20, 2.25), "anuncios": (16, 15), "tiendas": 0.4},
    "pollo": {"titulos": ["Pollo troceado caja 10 lb", "Caja de pollo troceado 10 lb", "Pollo troceado 10 lb caja"],
              "precio": (22.40, 19.30), "anuncios": (11, 16), "tiendas": 0.45},
    "ventilador": {"titulos": ["Ventilador de pedestal 18 pulgadas", "Ventilador pedestal 18\"", "Ventilador de pie 18 pulgadas"],
                   "precio": (48.0, 38.5), "anuncios": (14, 19), "tiendas": 0.5},
    "freidora": {"titulos": ["Freidora de aire 5 L", "Freidora de aire 5 litros", "Air fryer freidora de aire 5L"],
                 "precio": (68.0, 67.0), "anuncios": (10, 11), "tiendas": 0.5},
}
PROVINCIAS = ["La Habana"] * 6 + ["Santiago de Cuba", "Matanzas", "Holguín"]
TIENDAS = ["Tienda Vedado", "Mercadito Online", "Bodega Express", "Casa del Hogar", "Distribuidora Oeste"]


def _interp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def generar() -> dict[str, list[dict]]:
    rng = random.Random(2026)
    filas: dict[str, list[dict]] = {"demo_clasificados": [], "demo_tiendas": []}
    for clave, p in PRODUCTOS.items():
        for s in range(SEMANAS):
            t = s / (SEMANAS - 1)
            captura = FIN - timedelta(days=7 * (SEMANAS - 1 - s) + 1)
            n = round(_interp(*p["anuncios"], t))
            usd = _interp(*p["precio"], t)
            for k in range(n):
                tienda = rng.random() < p["tiendas"]
                fuente = "demo_tiendas" if tienda else "demo_clasificados"
                ruido = rng.uniform(-0.05, 0.05)
                if tienda:
                    precio, moneda = round(usd * (1 + ruido) * 1.03, 2), "USD"     # las tiendas cobran algo más
                    vendedor = TIENDAS[k % len(TIENDAS)]
                else:
                    precio, moneda = round(usd * TASA[s] * (1 + ruido) / 10) * 10, "CUP"
                    vendedor = f"vendedor_{clave}_{rng.randint(1, 40)}"
                filas[fuente].append({
                    "listing_id": f"{clave}-{s}-{k}",
                    "url": f"https://ejemplo.invalid/{fuente}/{clave}/{s}/{k}",
                    "title": p["titulos"][k % len(p["titulos"])],
                    "seller": vendedor,
                    "province": PROVINCIAS[(k + s) % len(PROVINCIAS)],
                    "price": precio,
                    "currency": moneda,
                    "availability": "disponible",
                    "condition": "nuevo",
                    "captured_at": captura.isoformat(),
                    "published_at": (captura - timedelta(days=rng.randint(0, 5))).isoformat(),
                })
    # Ruido realista en la última semana: otra presentación, otro producto con nombre parecido y un precio anómalo.
    ultima = (FIN - timedelta(days=1)).isoformat()
    filas["demo_clasificados"] += [
        {"listing_id": "aceite-5l", "title": "Aceite de girasol 5 litros", "seller": "mayorista_1", "province": "La Habana",
         "price": 17500, "currency": "CUP", "captured_at": ultima},
        {"listing_id": "aceite-motor", "title": "Aceite de motor 1 L", "seller": "piezas_auto", "province": "La Habana",
         "price": 4500, "currency": "CUP", "captured_at": ultima},
        {"listing_id": "pollo-outlier", "title": "Pollo troceado caja 10 lb", "seller": "vendedor_x", "province": "La Habana",
         "price": 95000, "currency": "CUP", "captured_at": ultima},
    ]
    for fuente, rows in filas.items():
        nombre = "Clasificados" if fuente == "demo_clasificados" else "Tiendas online"
        for r in rows:
            r["source_id"] = fuente
            r["source_name"] = f"{nombre} (DEMO SINTÉTICA)"
    return filas


def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    for fuente, rows in generar().items():
        ruta = SALIDA / f"{fuente}.jsonl"
        ruta.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"{ruta}: {len(rows)} observaciones")


if __name__ == "__main__":
    main()
