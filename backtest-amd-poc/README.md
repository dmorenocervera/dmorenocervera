# Backtest AMD + retesteo del PoC

Setup de **Acumulación → Manipulación → Distribución** con entrada en el retesteo del PoC del rango:

1. **Contexto**: impulso fuerte previo (opcional).
2. **Acumulación**: rango lateral con al menos 2 máximos (pivotes) en su zona alta y 2 mínimos en su zona baja. Se calcula su Volume Profile (rango fijo, con velas de 1m) y su **PoC**.
3. **Manipulación**: el precio barre la liquidez de un lado del rango (SSL abajo → largo; BSL arriba → corto). Las dos direcciones se evalúan en el mismo recorrido.
4. **Confirmación** (`--confirm-mode`): `opuesto` = cierre fuera del rango por el lado contrario al barrido (por defecto); `poc` = cierre al otro lado del PoC. Un pinchazo que no se confirma no cierra el rango: se absorbe y el rango se amplía (rango elástico).
5. **Entrada** (`--entry-mode`): `close` = a mercado tras la vela de reacción (mecha que perfora el PoC y cierre por encima); `break` = orden stop en el máximo de esa vela; `limit` = orden límite en el PoC al primer toque. SL bajo el mínimo local del retesteo (o PoC − x·ATR en `limit`).
6. **Salida**: se comparan 12 variantes (directriz alcista, directriz o estructura previa, estructura previa al rango, origen del impulso, proyección del rango x1/x2, 2R/3R/5R, parcial 50 % a 2R + BE, trailing ATR y trailing por estructura).

Por defecto solo largos. Con `--shorts` se añaden los cortos, que son el espejo exacto (barrido del BSL y retesteo del PoC como resistencia).

**Modo apertura NY** (`--ny-open`): el rango tiene que llegar intacto a las 9:30 ET y la manipulación ocurrir en la primera hora de sesión.

## Uso

```bash
pip install -r requirements.txt

# Oro (Dukascopy, volumen de ticks), varias temporalidades
python run_backtest.py --duka XAUUSD --start 2020-01-01 --end 2025-12-31 --tf 5min,15min,1h --cost 0.35

# Nasdaq 100 en modo apertura de Nueva York
python run_backtest.py --duka USATECHIDXUSD --start 2020-01-01 --end 2025-12-31 --tf 5min --ny-open --no-impulse --cost 1.5

# CSV propio de velas de 1m (TradingView, MT5, NinjaTrader...): columnas time, open, high, low, close, volume
python run_backtest.py --csv data/NQ_1m.csv --tf 5min --cost 0.5
```

`--cost` es el coste total de ida y vuelta en unidades de precio (spread + comisión + slippage). Las salidas van a `reports/`:

- `*.html`: informe con métricas por variante, dentro/fuera de muestra (70/30), por lado, sesión y año, curva de equity y gráficos de ejemplos.
- `*_trades.csv`: todas las operaciones.

Todos los umbrales del detector están en `Params` (`amd/strategy.py`).

## Supuestos del backtest

- Entrada en la apertura de la vela siguiente a la señal.
- Salidas simuladas con velas de 1m; si SL y TP caen en la misma vela de 1m, cuenta como SL.
- Cierre por tiempo a los 3 días.
- Los resultados se miden en R (múltiplos del riesgo); `ret_1pct_risk` es el retorno compuesto arriesgando el 1 % por operación.
- `t_stat` > 2 es el mínimo para empezar a creerse que la ventaja no es casualidad.

## Tests

```bash
python -m pytest -q
```

## Dashboard interactivo

Dos vistas:

- **Automático**: el detector busca los setups con las reglas y parámetros del panel.
- **Manual · replay**: avanzas vela a vela sin ver el futuro, marcas tú el rango y la entrada, y el motor calcula el resultado con las mismas salidas. También mide cuántos de tus setups encuentra el detector automático y cuántas señales del detector no habrías tomado tú.


`dashboard/index.html` + `dashboard/engine.js`: el mismo motor portado a JavaScript (resultados idénticos a Python) que se ejecuta en el navegador. Cargas un CSV de velas de 1m, ajustas los parámetros por fase y ves al momento métricas, curva de equity, embudo del setup, desglose y el gráfico de cada operación. Se abre directamente en el navegador, sin servidor.
