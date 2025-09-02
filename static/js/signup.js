document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('signupForm');
    
    function showError(message) {
        console.error('Error:', message);
    }

    function validateInputs(email, password, confirmPassword) {
        // 이메일 검사
        if (!email) {
            showError('이메일을 입력해주세요.');
            return false;
        }

        // 비밀번호 검사
        if (!password) {
            showError('비밀번호를 입력해주세요.');
            return false;
        }

        // 비밀번호 확인 검사
        if (!confirmPassword) {
            showError('비밀번호 확인을 입력해주세요.');
            return false;
        }

        // 비밀번호에 개행문자가 있는지 확인
        if (password.includes('\n') || password.includes('\r') || password.includes(' ')|| password.includes('\t')) {
            showError('올바르지 않은 비밀번호입니다.');
            return false;
        }

        // 비밀번호와 비밀번호 확인이 일치하는지 확인
        if (password !== confirmPassword) {
            showError('비밀번호가 일치하지 않습니다.');
            return false;
        }

        return true;
    }

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const email = document.getElementById('email').value.trim();
        const password = document.getElementById('password').value;
        const confirmPassword = document.getElementById('confirmPassword').value;
        
        // 입력값 검증
        if (!validateInputs(email, password, confirmPassword)) {
            return;
        }

        // form을 직접 제출
        const formData = new FormData();
        formData.append('email', email);
        formData.append('password', password);
        
        // 서버로 form 데이터 전송
        form.method = 'POST';
        form.action = '/api/signup';
        form.submit();
    });
});
