"""Test defaults: no background model downloads and no 'models pending' gate, so API tests
run on CI machines without the ~1.1GB of models. Model-dependent tests skip themselves;
tests/test_model_store.py covers the download / gate behaviour explicitly."""
import os

os.environ.setdefault('PIXLITE_AUTO_DOWNLOAD_MODELS', '0')
os.environ.setdefault('PIXLITE_REQUIRE_MODELS', '0')
