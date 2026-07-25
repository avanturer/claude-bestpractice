.PHONY: check test doctor lint budget docs clean help

PY := python3

help:
	@echo "make check   - everything CI runs: lint, tests, doctor, budget"
	@echo "make test    - unit and end-to-end tests"
	@echo "make doctor  - prove each gate fires by attempting a known-bad action"
	@echo "make lint    - syntax check and the stdlib-only constraint"
	@echo "make docs    - the LLM-first documentation gate"
	@echo "make budget  - assert the always-on context budget is not exceeded"

# One definition of done, identical in every session, with no --no-verify path.
# Eight parallel sessions must not converge on eight notions of finished.
check: lint docs test doctor budget
	@echo ""
	@echo "check: all green"

docs:
	@$(PY) tools/check_docstrings.py --all

test:
	@$(PY) -m unittest discover -s tests -t tests

doctor:
	@$(PY) plugin/bin/founder-os-doctor

lint:
	@$(PY) -m compileall -q plugin/lib plugin/bin tests tools
	@$(PY) tools/check_stdlib_only.py

budget:
	@$(PY) tools/check_budget.py

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@echo "clean"
