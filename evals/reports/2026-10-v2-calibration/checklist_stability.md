# Requirement checklist extraction stability

Run 2026-10-06 with the registry grading model (openai/gpt-6-luna@high) through `requirement_coverage.extract_requirements`, cache cleared before every call. Instructions: the two dataset v2 assignments. Gauntlet G-7: re-extracting the same instructions must keep >= 90% of requirement ids.

## CSC 151 Java (OrderTotal)

- run 1: generation `gen-1791344347-DfQPetQFctHWwh95zoyh`, model `openai/gpt-6-luna`, 5 requirements: R1 (secondary); R2 (core); R3 (core); R4 (core); R5 (core)
- run 2: generation `gen-1791344351-Bl0PXPeYxyaU97W1d550`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 3: generation `gen-1791344354-2mv5pXMXgDdbLZNYYB73`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 4: generation `gen-1791344357-qiMxfbp7ke9wQA71zZDU`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 5: generation `gen-1791344361-BbtRpQwZyZuE6stVC9AX`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 6: generation `gen-1791344366-X3zEPVfGKverVqfdd6eP`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)

Minimum pairwise id-set Jaccard over 6 runs: **1.000**

Run 1 checklist text:

  - R1: Use a Scanner to read an item price as a double and a quantity as an int.
  - R2: Compute the subtotal by multiplying the price by the quantity.
  - R3: When the quantity is 10 or more, apply a 10% discount to the subtotal.
  - R4: Add 7% sales tax to the discounted subtotal.
  - R5: Print the subtotal, tax, and total on three lines, each formatted with two decimal places and labeled exactly as specified.

## CSC 134 C++ (payroll)

- run 1: generation `gen-1791344370-tmS7dp78vViIWLNMY3Ov`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 2: generation `gen-1791344374-2fZDTferLYH6DHY1eURT`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 3: generation `gen-1791344378-NQRBNmSIpz0kMyVrkTlV`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 4: generation `gen-1791344381-V1KqO7yiIYGxRHkZo3wF`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 5: generation `gen-1791344390-zTDeMf9UOyZeld2lf62i`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)
- run 6: generation `gen-1791344393-VfKvqJILjWNn1RNWurxN`, model `openai/gpt-6-luna`, 5 requirements: R1 (core); R2 (core); R3 (core); R4 (core); R5 (core)

Minimum pairwise id-set Jaccard over 6 runs: **1.000**

Run 1 checklist text:

  - R1: Prompt for and read hours worked and hourly pay rate as doubles.
  - R2: Re-prompt until hours are between 0 and 80 inclusive and the rate is greater than 0.
  - R3: Declare `calculatePay(double hours, double rate)` above `main`, define it below `main`, and call it to compute gross pay.
  - R4: Calculate overtime pay at 1.5 times the hourly rate for hours worked over 40.
  - R5: Print gross pay in the format `Gross pay: $X.XX`, with two decimal places.

Total cost: $0.0025
