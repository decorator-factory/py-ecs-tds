python -m ruff check --fix game/ tests/
python -m ruff format game/ tests/
python -m isort game/ tests/

pushd frontend
npm run lint
popd