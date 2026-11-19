# Word test-report generator

Python, pandas and docxtpl. Real customer records, equipment serials and professional signatures are not included.

From this directory:

```powershell
python -m pip install pandas openpyxl docxtpl
python generate_example_inputs.py
python bulkWordCreator.py
```

The generator creates a synthetic Excel service order and a neutral Word template. Its output is example data, not a device test certificate. Populate a real report only with verified test methods, measurements and conclusions.
