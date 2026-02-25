import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Float, Box, Edges } from '@react-three/drei';
import * as THREE from 'three';

export const Chip3D = () => {
  const meshRef = useRef<THREE.Group>(null);

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = state.clock.getElapsedTime() * 0.2;
    }
  });

  return (
    <Float speed={2} rotationIntensity={0.5} floatIntensity={1}>
      <group ref={meshRef}>
        {/* Base Substrate - Dark Silicon */}
        <Box args={[4, 0.2, 4]} position={[0, -0.6, 0]}>
          <meshStandardMaterial color="#111" roughness={0.8} />
          <Edges color="#333" />
        </Box>

        {/* Die logic layer */}
        <Box args={[3.2, 0.4, 3.2]} position={[0, -0.3, 0]}>
          <meshStandardMaterial color="#0A0A0A" roughness={0.6} metalness={0.8} />
          <Edges color="#00D1FF" />
        </Box>

        {/* Top Active Matrix - Glowing */}
        <Box args={[2.5, 0.2, 2.5]} position={[0, 0, 0]}>
          <meshStandardMaterial
            color="#00FF88"
            emissive="#00FF88"
            emissiveIntensity={0.5}
            roughness={0.2}
            metalness={0.8}
          />
          <Edges color="#fff" />
        </Box>

        {/* Floating AI logic fragments */}
        {[[-1, 1, -1], [1, 1, 1], [-1, 1, 1], [1, 1, -1]].map((pos, i) => (
          <Float key={i} speed={3} rotationIntensity={2} floatIntensity={2}>
            <Box args={[0.3, 0.3, 0.3]} position={pos as [number, number, number]}>
              <meshStandardMaterial color="#00D1FF" emissive="#00D1FF" emissiveIntensity={0.8} />
            </Box>
          </Float>
        ))}
      </group>
    </Float>
  );
};
