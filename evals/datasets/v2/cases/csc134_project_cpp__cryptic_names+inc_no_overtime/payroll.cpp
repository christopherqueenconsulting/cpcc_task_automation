// Program: Payroll Calculator
// Author: Ada Example
// Purpose: Validates hours and rate, then computes gross pay with overtime.
#include <iostream>
#include <iomanip>
using namespace std;

// Weekly hours before overtime applies
const double OVERTIME_THRESHOLD = 40.0;
// Overtime pay multiplier
const double OVERTIME_MULTIPLIER = 1.5;
// Maximum hours allowed in one week
const double MAX_HOURS = 80.0;

// Computes gross pay, including overtime
double calculatePay(double hours, double rate);

int main() {
    double HW = 0.0;
    double R = 0.0;

    // Read hours until they are within the allowed range
    cout << "Enter hours worked: ";
    cin >> HW;
    while (HW < 0 || HW > MAX_HOURS) {
        cout << "Hours must be between 0 and 80. Enter hours worked: ";
        cin >> HW;
    }

    // Read the pay rate until it is positive
    cout << "Enter hourly rate: ";
    cin >> R;
    while (R <= 0) {
        cout << "Rate must be greater than 0. Enter hourly rate: ";
        cin >> R;
    }

    // Compute and display the gross pay
    double grossPay = calculatePay(HW, R);
    cout << fixed << setprecision(2);
    cout << "Gross pay: $" << grossPay << endl;

    return 0;
}

// Pays regular rate up to the threshold and overtime above it
double calculatePay(double hours, double rate) {
    return hours * rate;
}
