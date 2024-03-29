import sys
from struct import unpack

class Packet:
    """
    Primary data structure
    Contains IP, UDP, and ICMP headers
    """
    IP, UDP, ICMP, time = None, None, None, 0

    def __init__(self):
        self.ICMP = ICMP()
        self.time = 0

    def set_IP(self, src_ip, dst_ip, id, frag_offset, flags, ttl, protocol):
        self.IP = IP(src_ip, dst_ip, id, frag_offset, flags, ttl, protocol)
        
    def set_ICMP(self, type, code, seq, o_seq, src_port, dst_port):
        self.ICMP = ICMP(type, code, seq, o_seq, src_port, dst_port)

    def set_UDP(self, src_port, dst_port):
        self.UDP = UDP(src_port, dst_port)

class IP:
    """
    Holds information such as IP addresses and fragment offset
    """
    src_ip, dst_ip, id, frag_offset, flags, ttl, protocol = 0, 0, 0, 0, 0, 0, 0

    def __init__(self, src_ip, dst_ip, id, frag_offset, flags, ttl, protocol):
        self.src_ip, self.dst_ip, self.id, self.frag_offset, self.flags, self.ttl, self.protocol = src_ip, dst_ip, id, frag_offset, flags, ttl, protocol

class UDP:
    """
    Holds port information
    """
    src_port, dst_port = 0, 0

    def __init__(self, src_port, dst_port):
        self.src_port, self.dst_port = src_port, dst_port

class ICMP:
    """
    Holds information originally sent in ICMP request
    """
    type, code, seq, o_seq, src_port, dst_port = 0, 0, 0, 0, 0, 0

    def __init__(self, type=0, code=0, seq=0, o_seq=0, src_port=0, dst_port=0):
        self.type, self.code, self.seq, self.o_seq, self.src_port, self.dst_port = type, code, seq, o_seq, src_port, dst_port

def get_packets(file):
    """
    Handles errors in parsing multiple packets
    Final packet returns None, and then generator is finished
    """
    while True:
        packet = parse_packet(file)
        if not packet: return
        yield packet

def parse_packet(file):
    """
    1. Reads packet header
    2. Reads IP header
    3. Manages ICMP/UDP header
        - Check for TTL Exceeded
        - Check for other ICMP
    """
    packet = Packet()

    # Packet header
    buffer = file.read(16)
    if len(buffer) < 16:
        return None
    ts_sec, ts_usec, incl_len, _ = unpack('I I I I', buffer)
    packet.time = ts_sec*1e3 + ts_usec/1e6 # In milliseconds

    # Skip Ethernet header
    buffer = file.read(14)

    # IP header
    buffer = file.read(20)
    _, _, _, id, fragment, ttl, protocol, _, src_ip, dst_ip = unpack('>B B H H H B B H I I', buffer)
    flags = (fragment & 0xE000) >> 12
    fragment_offset = (fragment & 0x1FFF) << 3 # Isolate offset from flags
    dst_ip = '.'.join(str(dst_ip >> (i << 3) & 0xFF) for i in reversed(range(4)))
    src_ip = '.'.join(str(src_ip >> (i << 3) & 0xFF) for i in reversed(range(4)))
    packet.set_IP(src_ip, dst_ip, id, fragment_offset, flags, ttl, protocol)

    if protocol == 1: # ICMP
        position = 0
        buffer = file.read(8)
        type, code, _, _, _ = unpack('B B H H H', buffer)
        seq, o_seq, src_port, dst_port = None, None, None, None

        if type == 3 or type == 11: # TTL Exceeded
            position += 20
            buffer = file.read(20)
            protocol = buffer[9]
            ihl = (buffer[0] & 0x0F) << 2
            if ihl > 20: file.read(ihl - 20)
            seq, src_port, dst_port = None, None, None
            if protocol == 17: # Linux
                position += 8
                buffer = file.read(8)
                src_port, dst_port, _ = unpack('>H H I', buffer)
            elif protocol == 1: # Windows
                position += 8
                buffer = file.read(8)
                _, _, o_seq = unpack('>I H H', buffer)
        elif type == 0: # Echo Response
            _, _, o_seq = unpack('>I H H', buffer)
        elif type == 8: # Echo Request
            _, _, seq = unpack('>I H H', buffer)
        packet.set_ICMP(type, code, seq, o_seq, src_port, dst_port)
        file.read(incl_len - 42 - position)
    elif packet.IP.protocol == 17: # UDP
        buffer = file.read(8)
        src_port, dst_port, _ = unpack('>H H I', buffer)
        packet.set_UDP(src_port, dst_port)
        file.read(incl_len - 42)
    else: # Other (TCP, etc.)
        file.read(incl_len - 34)

    return packet

def is_linux(packets):
    """
    Searches packets to find either source port or original sequence number
    These are mutually exclusive in parse_packet() thus indicating OS
    """
    for packet in packets:
        if (packet.ICMP.src_port): return True # Linux
        elif (packet.ICMP.o_seq): return False # Windows

def print_routers(packets, dst_ip, linux):
    """
    Iterates through all pairs of packets
    Matches according to OS
    """
    routers = {}
    for i, a in enumerate(packets):
        if linux and a.IP.protocol == 17:
            for b in packets[i:]:
                if (b.ICMP.type == 11 or b.ICMP.type == 3) and a.UDP.src_port == b.ICMP.src_port:
                    key, rtt = b.IP.src_ip, b.time - a.time
                    if key not in routers:
                        routers[key] = [rtt]
                    else:
                        routers[key] += [rtt]
                        
        if not linux and a.ICMP.type == 8:
            for b in packets[i:]:
                if (b.ICMP.type == 0 or b.ICMP.type == 11) and a.ICMP.seq == b.ICMP.o_seq:
                    key, rtt = b.IP.src_ip, b.time - a.time
                    if key not in routers:
                        routers[key] = [rtt]
                    else:
                        routers[key] += [rtt]
    
    print('\nThe IP addresses of the intermediate destination nodes:')
    for i, ip in enumerate(key for key in routers if key != dst_ip):
        print(f'\trouter {i + 1}: {ip}')

    return routers

def print_summary(packets, linux):
    """
    1. Determines start and end IP (reversed in linux)
    2. Finds intermediate routers
    3. Shows included protocols (ICMP and/or UDP)
    4. Gathers fragments (if needed)
    5. Calculates RTT and SD
    """
    for packet in packets:
        if linux and packet.ICMP.code == 3:
            src_ip, dst_ip = packet.IP.dst_ip, packet.IP.src_ip
            break
        elif not linux and packet.ICMP.type == 8:
            src_ip, dst_ip = packet.IP.src_ip, packet.IP.dst_ip
            break
    print(f'The IP address of the source node: {src_ip}')
    print(f'The IP address of the destination node: {dst_ip}')

    routers = print_routers(packets, dst_ip, linux)

    protocols = {packet.IP.protocol for packet in packets}
    print(f'\nThe values in the protocol field of IP headers:')
    if 1 in protocols: print('\t1: ICMP')
    if 17 in protocols: print('\t17: UDP')
    print()

    for i, a in enumerate(packets):
        if a.IP.flags == 2: # More fragments
            for j, b in enumerate(packets[i:]):
                if b.IP.flags == 0: # Last fragment
                    print(f'The number of fragments created from the original datagram id {b.IP.id} is: {j+1}')
                    print(f'The offset of the last fragment is: {b.IP.frag_offset}\n')
                    break

    for ip in routers:
        mean = sum(routers[ip])/len(routers[ip])
        sd = (sum([(x - mean) ** 2 for x in routers[ip]]) / len(routers[ip])) ** 0.5
        print(f'The avg RTT between {src_ip} and {ip} is: {mean:.3f}ms, the s.d. is: {sd:.3f}ms')

def main():
    """
    1. Opens .pcap file
    2. Reads packets until eof
    3. Prints summary analysis of packets
    """
    if len(sys.argv) != 2:
        print('usage: a3.py <filename>')
        return
    
    file = open(sys.argv[1], 'rb')
    file.read(24)
   
    packets = list(get_packets(file))
    print_summary(packets, is_linux(packets))

    file.close()

if __name__ == '__main__':
    main()
