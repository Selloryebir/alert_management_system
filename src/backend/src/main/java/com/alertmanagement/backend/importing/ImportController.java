package com.alertmanagement.backend.importing;

import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/v1/imports")
class ImportController {

    private final ImportService importService;

    ImportController(ImportService importService) {
        this.importService = importService;
    }

    @PostMapping(path = "/preview", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    ImportBatchSummary preview(
            @RequestParam("file") MultipartFile file,
            @RequestParam(value = "mapping", required = false) String mapping) {
        return importService.preview(file, mapping);
    }

    @PostMapping("/{batchId}/confirm")
    ImportBatchSummary confirm(@PathVariable UUID batchId) {
        return importService.confirm(batchId);
    }

    @GetMapping("/{batchId}")
    ImportBatchSummary get(@PathVariable UUID batchId) {
        return importService.get(batchId);
    }
}
