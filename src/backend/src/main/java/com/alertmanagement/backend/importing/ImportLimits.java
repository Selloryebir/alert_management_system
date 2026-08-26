package com.alertmanagement.backend.importing;

final class ImportLimits {
    static final long MAX_FILE_BYTES = 50L * 1024 * 1024;
    static final int MAX_ROWS = 100_000;
    static final int MAX_COLUMNS = 256;
    static final int MAX_SHEETS = 8;
    static final int MAX_CELL_CHARACTERS = 4_096;
    static final int MAX_HEADER_CHARACTERS = 120;
    static final int MAX_MAPPING_BYTES = 32 * 1024;
    static final int MAX_CORRECTIONS_BYTES = 1024 * 1024;
    static final int MAX_CORRECTION_ROWS = 1_000;
    static final int MAX_ERRORS = 1_000;
    static final int MAX_ACTIONABLE_ROWS = 200;

    private ImportLimits() {
    }
}
