/*
 * Program: Shopping Cart
 * Author: Ben Sample
 * Purpose: Totals the prices of a cart and applies a coupon on large orders.
 */
import java.util.ArrayList;
import java.util.Scanner;

public class ShoppingCart {
    // Share taken off when the coupon applies
    public static final double COUPON_RATE = 0.10;
    // Smallest total that earns the coupon
    public static final double COUPON_MINIMUM = 100.0;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);
        ArrayList<Double> prices = new ArrayList<>();

        // Read prices until the user enters 0, asking again for a negative price
        System.out.print("Enter a price (0 to finish): ");
        double price = input.nextDouble();
        while (price != 0) {
            if (price < 0) {
                System.out.print("Price cannot be negative. Enter a price (0 to finish): ");
            } else {
                prices.add(price);
                System.out.print("Enter a price (0 to finish): ");
            }
            price = input.nextDouble();
        }

        if (prices.isEmpty()) {
            System.out.println("Cart is empty");
        } else {
            double total = totalPrice(prices);
            System.out.println("Items: " + prices.size());
            System.out.printf("Total: %.2f%n", total);
            System.out.printf("Final: %.2f%n", applyCoupon(total));
        }

        input.close();
    }

    // Adds up every price in the cart
    public static double totalPrice(ArrayList<Double> prices) {
        double sum = 0.0;
        for (int i = 0; i < prices.size(); i++) {
            sum += prices.get(i);
        }
        return sum;
    }

    // Takes the coupon off when the total is large enough
    public static double applyCoupon(double total) {
        if (total >= COUPON_MINIMUM) {
            return total - (total * COUPON_RATE);
        }
        return total;
    }
}
