/*
 * Program: Password Check
 * Author: Dee Placeholder
 * Purpose: Checks that a password is long enough and contains a digit.
 */
import java.util.Scanner;

public class PasswordCheck {
    // Shortest password that is accepted
    public static final int MIN_LENGTH = 8;

    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        // Read the password
        System.out.print("Enter a password: ");
        String password = input.nextLine();

        // Look for at least one digit
        boolean hasDigit = false;
        for (int i = 0; i < password.length(); i++) {
            if (Character.isDigit(password.charAt(i))) {
                hasDigit = true;
            }
        }

        // The password needs the minimum length and a digit
        if (password.length() >= MIN_LENGTH && hasDigit) {
            System.out.println("Valid");
        } else {
            System.out.println("invalid");
        }

        input.close();
    }
}
