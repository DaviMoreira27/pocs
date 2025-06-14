#include <stdio.h>
#include <stdlib.h>

#define MAX_LINE 1024

int main() {
    char filename[256];
    int num_lines;

    printf("Enter filename: ");
    fflush(stdout);

    if (scanf("%255s", filename) != 1) {
        fprintf(stderr, "Failed to read filename\n");
        exit(EXIT_FAILURE);
    }

    printf("How many lines do you want to read? ");
    fflush(stdout);

    if (scanf("%d", &num_lines) != 1 || num_lines < 1) {
        fprintf(stderr, "Invalid number of lines\n");
        exit(EXIT_FAILURE);
    }

    FILE *fp = fopen(filename, "r");
    if (!fp) {
        fprintf(stderr, "Could not open file: %s\n", filename);
        exit(EXIT_FAILURE);
    }

    printf("\n--- BEGIN FILE OUTPUT ---\n");
    char line[MAX_LINE];
    int count = 0;

    while (fgets(line, sizeof(line), fp) && count < num_lines) {
        printf("%s", line);
        count++;
    }

    printf("\n--- END FILE OUTPUT ---\n");

    fclose(fp);
    return 0;
}
