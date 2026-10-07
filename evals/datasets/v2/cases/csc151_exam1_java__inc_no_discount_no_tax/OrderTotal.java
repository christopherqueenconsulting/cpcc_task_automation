/*
 * Program: Order Total Calculator
 * Author: Cal Fixture
 * Purpose: Reads an item price and quantity, applies a bulk discount,
 *          adds sales tax and prints the order total.
 */
import java.util.Scanner;

public class OrderTotal {
    // Sales tax rate applied to every order
    public static final double TAX_RATE = 0.07;
    // Discount rate for bulk orders
    public static final double BULK_DISCOUNT_RATE = 0.10;
    // Minimum quantity that qualifies for the bulk discount
    public static final int BULK_QUANTITY = 10;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read the item price and quantity from the user
        System.out.print("Enter the item price: ");
        double itemPrice = input.nextDouble();
        System.out.print("Enter the quantity: ");
        int quantity = input.nextInt();

        // Calculate the subtotal before discount and tax
        double subtotal = itemPrice * quantity;

        double total = subtotal;

        // Display the results with two decimal places
        System.out.printf("Subtotal: $%.2f%n", subtotal);
        System.out.printf("Total: $%.2f%n", total);

        input.close();
    }
}
