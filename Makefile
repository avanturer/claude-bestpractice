.PHONY: check test doctor lint budget docs knowledge slop ratchet shipped clean help

PY := python3

help:
	@echo "make check   - everything CI runs: lint, tests, doctor, budget"
	@echo "make test    - unit and end-to-end tests"
	@echo "make doctor  - prove each gate fires by attempting a known-bad action"
	@echo "make lint    - syntax check and the stdlib-only constraint"
	@echo "make docs    - the LLM-first documentation gate"
	@echo "make slop    - catch the code an LLM writes that a person would not"
	@echo "make ratchet - lower the structural budgets to what is actually there"
	@echo "make shipped - refuse a change to plugin/ that no installed copy could receive"
	@echo "make knowledge - validate the decided layer and refresh its index"
	@echo "make budget  - assert the always-on context budget is not exceeded"

# One definition of done, identical in every session, with no --no-verify path.
# Eight parallel sessions must not converge on eight notions of finished.
check: lint docs slop polyglot knowledge shipped test doctor budget
	@echo ""
	@echo "check: all green"

# `claude plugin update` compares version strings and fetches nothing when they match, so
# a change under plugin/ that keeps the version is a change no installed copy can ever
# receive — while every attempt to get it reports success.
shipped:
	@$(PY) tools/check_shipped.py

# Defect classes have a permanent budget of zero. Structural debt is baselined on the
# first run and may only fall after that — a ratchet seeded at zero can never be
# satisfied by an existing codebase and gets disabled on day one.
slop:
	@$(PY) tools/check_slop.py --all

polyglot:
	@$(PY) tools/check_polyglot.py --all

ratchet:
	@$(PY) tools/check_slop.py --all --ratchet

docs:
	@$(PY) tools/check_docstrings.py --all

# Validates caps, entity anchors and decision records, then refreshes the index. The
# anchor check is what makes a rename fail loudly instead of leaving the layer
# describing a symbol that no longer exists.
knowledge:
	@$(PY) plugin/bin/claude-bp-knowledge index >/dev/null
	@$(PY) plugin/bin/claude-bp-knowledge validate

test:
	@$(PY) -m unittest discover -s tests -t tests

doctor:
	@$(PY) plugin/bin/claude-bp-doctor

lint:
	@$(PY) -m compileall -q plugin/lib plugin/bin tests tools
	@$(PY) tools/check_stdlib_only.py

budget:
	@$(PY) tools/check_budget.py

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@echo "clean"
