interface Authenticator {
    boolean authenticate(String configuredKey, String receivedKey);
}

class WifiAuthenticator implements Authenticator {
    public boolean authenticate(String configuredKey, String receivedKey) {
        return verifySharedKey(configuredKey, receivedKey);
    }

    private boolean verifySharedKey(String configuredKey, String receivedKey) {
        return configuredKey.equals(receivedKey);
    }
}
