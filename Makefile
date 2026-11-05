test:
	python -m pytest tests/

lint:
	python -m flake8 schwab bin examples tests

fix:
	autopep8 --in-place -r -a schwab
	#autopep8 --in-place -r -a tests
	#autopep8 --in-place -r -a examples

coverage:
	python -m coverage run --source=schwab -m pytest tests/
	python -m coverage html

dist: clean
	python -m build

release: clean test dist
	python -m twine upload dist/*

clean:
	rm -rf build dist docs-build schwab_py.egg-info __pycache__ htmlcov
