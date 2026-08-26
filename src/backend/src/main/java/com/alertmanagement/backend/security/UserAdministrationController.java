package com.alertmanagement.backend.security;

import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/admin/users")
class UserAdministrationController {
    private final UserAdministrationService service;

    UserAdministrationController(UserAdministrationService service) {
        this.service = service;
    }

    @GetMapping
    List<UserView> list() {
        return service.listUsers();
    }

    @PostMapping
    UserView create(@RequestBody(required = false) UserCreateRequest request) {
        return service.createUser(request);
    }

    @PatchMapping("/{userId}")
    UserView patch(@PathVariable UUID userId, @RequestBody(required = false) UserPatchRequest request) {
        return service.patchUser(userId, request);
    }

    @PostMapping("/{userId}/reset-password")
    UserView resetPassword(@PathVariable UUID userId,
            @RequestBody(required = false) PasswordResetRequest request) {
        return service.resetPassword(userId, request);
    }
}
