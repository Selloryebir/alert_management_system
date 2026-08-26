package com.alertmanagement.backend.security;

import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/projects/{projectId}/members")
class ProjectMembershipController {
    private final UserAdministrationService service;

    ProjectMembershipController(UserAdministrationService service) {
        this.service = service;
    }

    @GetMapping
    List<ProjectMemberView> list(@PathVariable UUID projectId) {
        return service.listMembers(projectId);
    }

    @PutMapping("/{userId}")
    ProjectMemberView put(@PathVariable UUID projectId, @PathVariable UUID userId,
            @RequestBody(required = false) ProjectMemberRequest request) {
        return service.putMember(projectId, userId, request);
    }

    @DeleteMapping("/{userId}")
    ResponseEntity<Void> delete(@PathVariable UUID projectId, @PathVariable UUID userId) {
        service.deleteMember(projectId, userId);
        return ResponseEntity.noContent().build();
    }
}
