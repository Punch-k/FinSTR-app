// MyShare - Main JavaScript

// Utility function to format currency
function formatCurrency(amount) {
    return '$' + parseFloat(amount).toFixed(2);
}

// Utility function to format percentage
function formatPercentage(value) {
    return (value * 100).toFixed(2) + '%';
}

// Utility function to show a message
function showMessage(elementId, message, type) {
    const element = document.getElementById(elementId);
    if (element) {
        element.className = `message ${type}`;
        element.textContent = message;
    }
}

// Utility function to get URL parameter
function getUrlParameter(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
}

// Check if user is logged in
function isLoggedIn() {
    return localStorage.getItem('userId') !== null;
}

// Logout user
function logout() {
    localStorage.removeItem('userId');
    localStorage.removeItem('username');
    localStorage.removeItem('userPassword');
    window.location.href = '/myshare/home';
}

// Auto-logout timer (optional - 30 minutes of inactivity)
let logoutTimer;
function resetLogoutTimer() {
    clearTimeout(logoutTimer);
    logoutTimer = setTimeout(() => {
        if (isLoggedIn()) {
            logout();
        }
    }, 30 * 60 * 1000); // 30 minutes
}

// Initialize logout timer on page load
document.addEventListener('DOMContentLoaded', () => {
    resetLogoutTimer();

    // Reset timer on user activity
    document.addEventListener('mousemove', resetLogoutTimer);
    document.addEventListener('keypress', resetLogoutTimer);
    document.addEventListener('click', resetLogoutTimer);
});

// API helper functions
async function apiRequest(endpoint, method = 'GET', data = null) {
    const options = {
        method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(endpoint, options);
    return response.json();
}

// Validate form inputs
function validateInput(value, type) {
    switch (type) {
        case 'username':
            return /^[a-zA-Z0-9._]{6,25}$/.test(value);
        case 'email':
            return /^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w{2,3}$/.test(value);
        case 'password':
            return value.length >= 8 && value.length <= 50;
        case 'symbol':
            return /^[a-zA-Z]{1,5}$/.test(value);
        case 'shares':
            return /^\d+$/.test(value) && parseInt(value) > 0;
        case 'price':
            return /^\d+(\.\d{1,2})?$/.test(value);
        case 'date':
            return /^\d{4}[- /.](0[1-9]|1[012])[- /.](0[1-9]|[12][0-9]|3[01])$/.test(value);
        default:
            return true;
    }
}

// Debounce function for search inputs
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Export functions for use in other scripts
window.MyShare = {
    formatCurrency,
    formatPercentage,
    showMessage,
    getUrlParameter,
    isLoggedIn,
    logout,
    apiRequest,
    validateInput,
    debounce
};
