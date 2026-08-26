package com.alertmanagement.backend.maintenance;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/data-backup-status")
class DataBackupController {
    private final DataBackupService service;

    DataBackupController(DataBackupService service) {
        this.service = service;
    }

    @GetMapping
    DataBackupStatusView status() {
        return service.status();
    }
}
