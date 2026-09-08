using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Text;

public static class GwapCertificateBootstrap {
    // First-use certificate retrieval only. No credentials, redirects or global TLS changes.
    public static string GetPublicCertificate(string url) {
        var uri = new Uri(url);
        if (uri.Scheme != "https" || uri.UserInfo != "" ||
            uri.AbsolutePath != "/api/v1/auth/server-certificate")
            throw new ArgumentException("Invalid certificate bootstrap URL");
        var request = (HttpWebRequest)WebRequest.Create(uri);
        request.Proxy = null;
        request.AllowAutoRedirect = false;
        request.Timeout = 15000;
        request.ReadWriteTimeout = 15000;
        request.ServerCertificateValidationCallback = (sender, cert, chain, errors) =>
            errors == SslPolicyErrors.None || errors == SslPolicyErrors.RemoteCertificateChainErrors;
        using (var response = (HttpWebResponse)request.GetResponse()) {
            if (response.StatusCode != HttpStatusCode.OK) throw new IOException("Certificate unavailable");
            using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8)) {
                var buffer = new char[32769];
                int count = reader.ReadBlock(buffer, 0, buffer.Length);
                if (count > 32768) throw new IOException("Certificate response too large");
                return new string(buffer, 0, count);
            }
        }
    }
}
