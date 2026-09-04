PY := python
SRC := src

.PHONY: help up down seed load check dedupe all reset break-delete break-drop break-stale demo-failure

help:
	@echo "make up        start SQL Server"
	@echo "make all       seed, load, dedupe, check"
	@echo "make load      incremental load into DuckDB"
	@echo "make dedupe    resolve duplicate customers"
	@echo "make check     run data quality checks"
	@echo "make demo-failure  break something and show the check failing"
	@echo "make reset     drop the warehouse and start over"

up:
	docker compose up -d --wait

down:
	docker compose down -v

seed:
	$(PY) $(SRC)/seed.py

load:
	$(PY) $(SRC)/load.py

dedupe:
	$(PY) $(SRC)/dedupe.py

check:
	$(PY) $(SRC)/validate.py

check-strict:
	$(PY) $(SRC)/validate.py --strict

all: seed load dedupe check

reset:
	rm -f warehouse/wh.duckdb

break-delete:
	$(PY) $(SRC)/break_data.py silent_delete

break-drop:
	$(PY) $(SRC)/break_data.py dropped_rows

break-stale:
	$(PY) $(SRC)/break_data.py stale_update

demo-failure: break-drop check
