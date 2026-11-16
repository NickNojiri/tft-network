.PHONY: setup test demo all
setup:
	pip install -e ".[gemini,dev]"
	cp -n .env.example .env || true
test:
	python -m pytest -q
demo:
	python -m tftnet demo
all:
	python -m tftnet all
