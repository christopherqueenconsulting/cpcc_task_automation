// Program: Payroll Calculator
// Author: Ada Example
// Purpose (résumé ✓, 日本語): Validates hours and rate, then computes gross pay with overtime.
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
    double hoursWorked = 0.0;
    double hourlyRate = 0.0;

    // Read hours until they are within the allowed range
    cout << "Enter hours worked: ";
    cin >> hoursWorked;
    while (hoursWorked < 0 || hoursWorked > MAX_HOURS) {
        cout << "Hours must be between 0 and 80. Enter hours worked: ";
        cin >> hoursWorked;
    }

    // Read the pay rate until it is positive
    cout << "Enter hourly rate: ";
    cin >> hourlyRate;
    while (hourlyRate <= 0) {
        cout << "Rate must be greater than 0. Enter hourly rate: ";
        cin >> hourlyRate;
    }

    // Compute and display the gross pay
    double grossPay = calculatePay(hoursWorked, hourlyRate);
    cout << fixed << setprecision(2);
    cout << "Gross pay: $" << grossPay << endl;

    return 0;
}

// Pays regular rate up to the threshold and overtime above it
double calculatePay(double hours, double rate) {
    double pay = 0.0;
    if (hours > OVERTIME_THRESHOLD) {
        double overtimeHours = hours - OVERTIME_THRESHOLD;
        pay = (OVERTIME_THRESHOLD * rate) + (overtimeHours * rate * OVERTIME_MULTIPLIER);
    } else {
        pay = hours * rate;
    }
    return pay;
}
