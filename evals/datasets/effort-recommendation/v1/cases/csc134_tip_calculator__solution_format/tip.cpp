// Program: Tip Calculator
// Author: Ben Sample
// Purpose: Computes a tip from the bill and a service rating.
#include <iostream>
#include <iomanip>
using namespace std;

// Tip rate for excellent service
const double EXCELLENT_TIP_RATE = 0.20;
// Tip rate for any other service
const double STANDARD_TIP_RATE = 0.15;
// Rating that counts as excellent service
const int EXCELLENT_RATING = 3;

int main() {
    double bill = 0.0;
    int rating = 0;

    // Read the bill and the rating
    cout << "Enter the bill amount: ";
    cin >> bill;
    cout << "Enter the service rating (1-3): ";
    cin >> rating;

    // Choose the tip rate from the rating
    double tipRate = STANDARD_TIP_RATE;
    if (rating == EXCELLENT_RATING) {
        tipRate = EXCELLENT_TIP_RATE;
    }

    // Show the tip with two decimal places
    cout << fixed << setprecision(2);
    cout << "Tip: $" << bill * tipRate << endl;
    return 0;
}
