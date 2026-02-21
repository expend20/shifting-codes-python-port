// Serial Number Checker -- Obfuscation Demo
//
// Functions marked with @obfuscate are pre-selected for obfuscation.
// Try applying Substitution + MBA + Bogus Control Flow, then Compile & Run
// or Export to see the result.
//
// Valid serial numbers for testing:
//   SHFT-0500-CODE-XRAY   (Basic tier)
//   DEMO-2500-LLVM-PASS   (Pro tier)
//   PROD-7000-OBFS-KEYS   (Enterprise tier)

extern int printf(const char *, ...);

// @obfuscate
static int check_serial(const char *serial) {
    // Verify length (expect 19 chars: XXXX-NNNN-XXXX-XXXX)
    int len = 0;
    while (serial[len] != '\0') len++;
    if (len != 19) return 0;

    // Check dashes at positions 4, 9, 14
    if (serial[4] != '-' || serial[9] != '-' || serial[14] != '-')
        return 0;

    // Compute a weighted checksum over all characters
    unsigned int sum = 0;
    for (int i = 0; i < 19; i++) {
        sum = sum * 31 + (unsigned char)serial[i];
    }

    // Accept if checksum matches any known product key
    return sum == 0x3EE56CB4u   // SHFT-0500-CODE-XRAY
        || sum == 0x3952CB47u   // DEMO-2500-LLVM-PASS
        || sum == 0xF36594C3u;  // PROD-7000-OBFS-KEYS
}

// @obfuscate
static int derive_license_tier(const char *serial) {
    // Extract the numeric segment (positions 5-8)
    int tier = 0;
    for (int i = 5; i < 9; i++) {
        char c = serial[i];
        if (c < '0' || c > '9') return -1;
        tier = tier * 10 + (c - '0');
    }
    if (tier < 1000) return 0;       // Basic
    if (tier < 5000) return 1;       // Pro
    return 2;                         // Enterprise
}

static const char *tier_name(int tier) {
    if (tier == 0) return "Basic";
    if (tier == 1) return "Pro";
    if (tier == 2) return "Enterprise";
    return "Unknown";
}

int main(int argc, char **argv) {
    if (argc != 2) {
        printf("Usage: serial_check <ABCD-1234-EFGH-5678>\n");
        return 1;
    }

    const char *serial = argv[1];

    if (!check_serial(serial)) {
        printf("Invalid serial number.\n");
        return 1;
    }

    int tier = derive_license_tier(serial);
    printf("Serial accepted -- license tier: %s\n", tier_name(tier));
    return 0;
}
