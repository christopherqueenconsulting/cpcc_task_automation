import java.util.Scanner;

public class OrderTotal {
    public static final double TAX_RATE = 0.07;
    public static final double BULK_DISCOUNT_RATE = 0.10;
    public static final int BULK_QUANTITY = 10;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        System.out.print("Enter the item price: ");
        double Item_Price = input.nextDouble();
        System.out.print("Enter the quantity: ");
        int quantity = input.nextInt();

        double subtotal = Item_Price * quantity;

        if (quantity >= BULK_QUANTITY) {
            subtotal = subtotal - (subtotal * BULK_DISCOUNT_RATE);
        }

        double tax = subtotal * TAX_RATE;
        double total = subtotal + tax;

        System.out.printf("Subtotal: $%.2f%n", subtotal);
        System.out.printf("Tax: $%.2f%n", tax);
        System.out.printf("Total: $%.2f%n", total);

        input.close();
    }
}
