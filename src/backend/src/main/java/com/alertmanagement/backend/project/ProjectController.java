package com.alertmanagement.backend.project;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/projects")
class ProjectController {

    private final ProjectService projectService;
    private final ManualAlarmService manualAlarmService;

    ProjectController(ProjectService projectService, ManualAlarmService manualAlarmService) {
        this.projectService = projectService;
        this.manualAlarmService = manualAlarmService;
    }

    @GetMapping
    List<ProjectView> list(
            @RequestParam(required = false) String q,
            @RequestParam(name = "include_archived", defaultValue = "false") boolean includeArchived) {
        return projectService.list(q, includeArchived);
    }

    @PostMapping
    ProjectView create(@RequestBody(required = false) ProjectRequest request) {
        return projectService.create(request);
    }

    @GetMapping("/{projectId}")
    ProjectView get(@PathVariable UUID projectId) {
        return projectService.get(projectId);
    }

    @PatchMapping("/{projectId}")
    ProjectView update(@PathVariable UUID projectId, @RequestBody(required = false) ProjectPatch patch) {
        return projectService.update(projectId, patch);
    }

    @PostMapping("/{projectId}/archive")
    ProjectView archive(@PathVariable UUID projectId) {
        return projectService.setArchived(projectId, true);
    }

    @PostMapping("/{projectId}/restore")
    ProjectView restore(@PathVariable UUID projectId) {
        return projectService.setArchived(projectId, false);
    }

    @DeleteMapping("/{projectId}")
    ResponseEntity<Void> delete(@PathVariable UUID projectId) {
        projectService.delete(projectId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/{projectId}/overview")
    ProjectOverview overview(@PathVariable UUID projectId) {
        return projectService.overview(projectId);
    }

    @GetMapping("/{projectId}/export")
    ResponseEntity<byte[]> export(@PathVariable UUID projectId) {
        ProjectService.ProjectManifest manifest = projectService.export(projectId);
        return ResponseEntity.ok()
                .contentType(new MediaType("application", "json", StandardCharsets.UTF_8))
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename(manifest.fileName(), StandardCharsets.UTF_8).build().toString())
                .body(manifest.content());
    }

    @PostMapping("/{projectId}/manual-alarms")
    ManualAlarmView createManualAlarm(
            @PathVariable UUID projectId, @RequestBody(required = false) ManualAlarmRequest request) {
        return manualAlarmService.create(projectId, request);
    }

    @GetMapping("/{projectId}/manual-alarms")
    List<ManualAlarmView> listManualAlarms(@PathVariable UUID projectId) {
        return manualAlarmService.list(projectId);
    }

    @PatchMapping("/{projectId}/manual-alarms/{recordId}")
    ManualAlarmView updateManualAlarm(
            @PathVariable UUID projectId, @PathVariable UUID recordId,
            @RequestBody(required = false) ManualAlarmPatch request) {
        return manualAlarmService.update(projectId, recordId, request);
    }

    @PostMapping("/{projectId}/manual-alarms/{recordId}/invalidate")
    ManualAlarmView invalidateManualAlarm(
            @PathVariable UUID projectId, @PathVariable UUID recordId,
            @RequestBody(required = false) ManualAlarmInvalidation request) {
        return manualAlarmService.invalidate(projectId, recordId, request);
    }
}
