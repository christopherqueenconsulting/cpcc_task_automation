// Program: Inventory Total
// Author: Ben Sample
// Purpose: Totals four items, discounting large quantities.
#include <iostream>
#include <iomanip>
using namespace std;

// Number of items to read
const int ITEM_COUNT = 4;
// Quantity that earns the discount
const int BULK_QUANTITY = 12;
// Discount rate for bulk quantities
const double BULK_DISCOUNT = 0.10;

// Computes the total for one item, including the bulk discount
double lineTotal(double price, int quantity);

int main() {
    double grandTotal = 0.0;
    int discounted = 0;

    for (int i = 1; i <= ITEM_COUNT; i++) {
        double price = 0.0;
        int quantity = 0;

        // Read the price until it is positive
        cout << "Enter price for item " << i << ": ";
        cin >> price;
        while (price <= 0) {
            cout << "Price must be greater than 0. Enter price for item " << i << ": ";
            cin >> price;
        }

        // Read the quantity until it is not negative
        cout << "Enter quantity for item " << i << ": ";
        cin >> quantity;
        while (quantity < 0) {
            cout << "Quantity cannot be negative. Enter quantity for item " << i << ": ";
            cin >> quantity;
        }

        // Add this item to the totals
        grandTotal += lineTotal(price, quantity);
        if (quantity >= BULK_QUANTITY) {
            discounted++;
        }
    }

    cout << fixed << setprecision(2);
    cout << "Items discounted: " << discounted << endl;
    cout << "Grand total: $" << grandTotal << endl;
    return 0;
}

// Price times quantity, less the discount for bulk quantities
double lineTotal(double price, int quantity) {
    double total = price * quantity;
    if (quantity >= BULK_QUANTITY) {
        total = total - (total * BULK_DISCOUNT);
    }
    return total;
}
