#include <stdio.h>

int main() {
    char nome[100];

    printf("Digite seu nome: \n");
    scanf("%99s\n", nome);

    printf("Olá, %s! Seja bem-vindo.\n", nome);

    return 0;
}
