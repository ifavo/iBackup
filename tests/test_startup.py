import socket


def test_local_listener_can_restart_after_serving_a_connection():
    from ibackup.__main__ import local_socket
    listener = local_socket(0)
    address = listener.getsockname()
    listener.listen(1)
    client = socket.create_connection(address)
    accepted, _ = listener.accept()
    accepted.shutdown(socket.SHUT_WR)
    assert client.recv(1) == b''
    client.close()
    accepted.close()
    listener.close()
    replacement = local_socket(address[1])
    assert replacement.getsockname() == address
    replacement.close()
